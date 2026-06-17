"""
多引擎池：维护若干个独立的浏览器上下文（"引擎"），
每个引擎拥有独立的 UA / 视口 / 指纹，并实现使用次数冷却与定期重建。

设计目标（反爬要点）：
1. UA 身份轮换  —— 每个引擎固定一个 UA，引擎之间互不相同
2. 多引擎爬取  —— 任务在 N 个引擎间轮询分配，单个引擎请求频率降低
3. 引擎冷却    —— 单引擎达到请求阈值后强制休眠一段时间
4. 指纹重建    —— 单引擎达到更高阈值后，销毁重建上下文，更换 UA/视口
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from dataclasses import dataclass, field

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from .config import LOCALES, LOGIN_STATE_PATH, TIMEZONES, USER_AGENTS, VIEWPORTS, CooldownConfig

logger = logging.getLogger("scraper.engine_pool")


@dataclass
class Engine:
    """单个采集引擎：一个独立的浏览器上下文 + 状态计数。"""

    engine_id: int
    context: BrowserContext
    page: Page
    user_agent: str
    request_count: int = 0       # 自上次冷却以来的请求数
    total_request_count: int = 0  # 自上次重建以来的总请求数
    cooling_until: float = 0.0    # 冷却结束时间戳（time.time()）

    def is_cooling(self) -> bool:
        return time.time() < self.cooling_until

    def remaining_cooldown(self) -> float:
        return max(0.0, self.cooling_until - time.time())


class EnginePool:
    """管理多个 Engine 实例，提供轮询获取可用引擎的能力。"""

    def __init__(self, cooldown: CooldownConfig, pool_size: int = 3, headless: bool = True):
        self.cooldown = cooldown
        self.pool_size = pool_size
        self.headless = headless

        self._playwright = None
        self._browser: Browser | None = None
        self._engines: list[Engine] = []
        self._rr_index = 0  # round-robin 指针
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def start(self) -> None:
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=self.headless)
        for i in range(self.pool_size):
            engine = await self._create_engine(i)
            self._engines.append(engine)
        logger.info("引擎池启动完成，共 %d 个引擎", len(self._engines))

    async def close(self) -> None:
        for engine in self._engines:
            await engine.context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        logger.info("引擎池已关闭")

    async def _create_engine(self, engine_id: int) -> Engine:
        """创建一个全新的浏览器上下文：随机分配 UA / 视口 / locale。"""
        ua = random.choice(USER_AGENTS)
        viewport = random.choice(VIEWPORTS)

        context_kwargs = dict(
            user_agent=ua,
            viewport=viewport,
            locale=random.choice(LOCALES),
            timezone_id=random.choice(TIMEZONES),
        )

        # 若已通过 save_login_state.py 保存过登录态，则复用，避免反复登录
        if os.path.exists(LOGIN_STATE_PATH):
            context_kwargs["storage_state"] = LOGIN_STATE_PATH
            logger.info("引擎 #%d 加载登录态：%s", engine_id, LOGIN_STATE_PATH)
        else:
            logger.warning(
                "引擎 #%d 未找到登录态文件（%s），将以未登录身份访问，"
                "搜索结果可能受限。请先运行 "
                "`python -m calibration.scraper.save_login_state`",
                engine_id,
                LOGIN_STATE_PATH,
            )

        context = await self._browser.new_context(**context_kwargs)

        # 反检测：隐藏 navigator.webdriver 等自动化特征标志，
        # 这是最容易被网站用来识别 Playwright/Selenium 的信号
        await context.add_init_script(
            """
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.chrome = window.chrome || { runtime: {} };
            Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh']});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
            """
        )

        page = await context.new_page()
        logger.info("引擎 #%d 创建完成 | UA=%s | viewport=%s", engine_id, ua[:40], viewport)
        return Engine(engine_id=engine_id, context=context, page=page, user_agent=ua)

    async def _recreate_engine(self, index: int) -> None:
        """达到重建阈值：销毁旧上下文，生成新指纹，实现“身份轮换”。"""
        old = self._engines[index]
        await old.context.close()
        new_engine = await self._create_engine(old.engine_id)
        self._engines[index] = new_engine
        logger.info("引擎 #%d 已重建（更换 UA/视口）", new_engine.engine_id)

    # ------------------------------------------------------------------
    # 核心调度逻辑
    # ------------------------------------------------------------------

    async def acquire(self) -> Engine:
        """
        轮询获取一个可用引擎。
        若所有引擎都在冷却中，则等待冷却时间最短的那个引擎醒来。
        """
        async with self._lock:
            while True:
                # 轮询查找一个不在冷却中的引擎
                for _ in range(len(self._engines)):
                    idx = self._rr_index % len(self._engines)
                    self._rr_index += 1
                    engine = self._engines[idx]

                    if not engine.is_cooling():
                        return engine

                # 全部冷却中：等待最短冷却时间的引擎
                min_wait = min(e.remaining_cooldown() for e in self._engines)
                logger.info("所有引擎均在冷却，等待 %.0f 秒", min_wait)
                await asyncio.sleep(min_wait + 1)

    async def release(self, engine: Engine) -> None:
        """
        任务完成后归还引擎：更新计数，必要时触发冷却或重建。
        """
        engine.request_count += 1
        engine.total_request_count += 1

        # 达到重建阈值：直接重建（重建后计数清零）
        idx = self._engines.index(engine)
        if engine.total_request_count >= self.cooldown.max_requests_before_recreate:
            await self._recreate_engine(idx)
            return

        # 达到冷却阈值：进入休眠
        if engine.request_count >= self.cooldown.max_requests_before_cooldown:
            cd = random.uniform(*self.cooldown.cooldown_seconds_range)
            engine.cooling_until = time.time() + cd
            engine.request_count = 0
            logger.info("引擎 #%d 进入冷却 %.0f 秒", engine.engine_id, cd)

    async def human_delay(self) -> None:
        """模拟人类操作间隔的随机延迟。"""
        delay = random.uniform(*self.cooldown.action_delay_range)
        await asyncio.sleep(delay)
