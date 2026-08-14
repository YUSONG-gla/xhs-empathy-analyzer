"""
小红书文本采集主流程（混合架构）。

使用方式：
    python -m calibration.scraper.xhs_scraper

采集逻辑：
1. 按 config.SCRAPE_PLAN 中定义的关键词逐一处理
2. 列表页：用 Playwright 打开搜索页，拦截搜索接口的网络响应直接拿笔记列表
   （该接口有 x-s/x-s-common 签名校验，httpx 无法直连，只能借浏览器之手）
3. 详情页：用 httpx 直接请求详情页，正则提取 meta 标签拿正文
   （详情页是普通页面请求，无签名要求，比 Playwright 渲染更快更轻量）
4. 粗筛（广告关键词、长度）后增量写入 CSV，防止中途失败丢失数据

前置条件：
- 先运行一次 `python -m calibration.scraper.save_login_state` 手动登录并保存登录态
- xhs_scraper.py 会同时把这个登录态喂给 Playwright（列表页）和 httpx（详情页）
"""

from __future__ import annotations

import asyncio
import csv
import logging
import random
from pathlib import Path

from .config import (
    AD_KEYWORDS,
    DETAIL_FETCH_COOLDOWN_EVERY,
    DETAIL_FETCH_COOLDOWN_SECONDS_RANGE,
    DETAIL_FETCH_DELAY_RANGE,
    LOGIN_STATE_PATH,
    MAX_TEXT_LENGTH,
    MIN_TEXT_LENGTH,
    OUTPUT_CSV,
    SCRAPE_PLAN,
    EnginePoolConfig,
    ScrapeTask,
)
from .engine_pool import EnginePool
from .http_detail_client import SessionBlockedError, build_client, fetch_detail, load_cookies
from .search_capture import capture_search_notes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("scraper.xhs")

CSV_FIELDS = [
    "post_id",
    "title",
    "content",
    "like_count",
    "category",
    "keyword",
]


def is_ad(text: str) -> bool:
    return any(kw in text for kw in AD_KEYWORDS)


def passes_length_filter(text: str) -> bool:
    return MIN_TEXT_LENGTH <= len(text) <= MAX_TEXT_LENGTH


class CsvWriter:
    """增量写入 CSV，支持去重（按标题）。"""

    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seen_titles: set[str] = set()
        self._init_file()

    def _init_file(self) -> None:
        if not self.path.exists():
            with open(self.path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
                writer.writeheader()
        else:
            with open(self.path, "r", encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    self._seen_titles.add(row.get("title", ""))

    def write(self, row: dict) -> bool:
        key = row["title"] or row["content"][:30]
        if key in self._seen_titles:
            return False
        self._seen_titles.add(key)
        with open(self.path, "a", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            writer.writerow(row)
        return True


async def process_task(
    pool: EnginePool,
    http_client,
    task: ScrapeTask,
    writer: CsvWriter,
    post_id_start: int,
) -> int:
    """处理单个关键词：列表页用 Playwright 捕获，详情页用 httpx 批量获取。"""
    post_id = post_id_start
    collected = 0

    # --- 第一步：列表页，Playwright 捕获网络响应 ---
    engine = await pool.acquire()
    try:
        notes = await capture_search_notes(engine.page, task.keyword, task.target_count)
    except Exception as exc:  # noqa: BLE001
        logger.error("[%s] 列表采集异常：%s", task.keyword, exc)
        notes = []
    finally:
        await pool.release(engine)

    if not notes:
        logger.warning("[%s] 未采集到任何笔记，跳过详情阶段", task.keyword)
        return 0

    # --- 第二步：详情页，httpx 逐条请求（带简单延迟与轻量冷却） ---
    # 注意：SessionBlockedError 不在这里捕获，要让它往外传播到 run()，
    # 因为这是账号级问题，继续跑剩下的笔记/关键词只会全部失败，应该整体提前终止。
    fetched_count = 0
    for note in notes:
        detail = await fetch_detail(http_client, note.note_id, note.detail_url())
        fetched_count += 1

        if detail is None:
            await asyncio.sleep(random.uniform(*DETAIL_FETCH_DELAY_RANGE))
            continue

        content = detail.content.strip()
        if not content or is_ad(content) or not passes_length_filter(content):
            await asyncio.sleep(random.uniform(*DETAIL_FETCH_DELAY_RANGE))
            continue

        row = {
            "post_id": post_id,
            "title": detail.title or note.title,
            "content": content,
            "like_count": note.like_count,  # 点赞数从搜索列表响应里取，已是精确数字
            "category": task.category,
            "keyword": task.keyword,
        }
        if writer.write(row):
            collected += 1
            post_id += 1

        # 轻量冷却：httpx 请求成本低，但累计到一定数量后仍主动休息一下
        if fetched_count % DETAIL_FETCH_COOLDOWN_EVERY == 0:
            cd = random.uniform(*DETAIL_FETCH_COOLDOWN_SECONDS_RANGE)
            logger.info("[%s] 详情请求达到 %d 次，冷却 %.0f 秒", task.keyword, fetched_count, cd)
            await asyncio.sleep(cd)
        else:
            await asyncio.sleep(random.uniform(*DETAIL_FETCH_DELAY_RANGE))

    logger.info("[%s] 处理完成，新增 %d 条（候选 %d 条）", task.keyword, collected, len(notes))
    return collected


async def run() -> None:
    cfg = EnginePoolConfig()
    pool = EnginePool(cfg.cooldown, pool_size=cfg.pool_size, headless=cfg.headless)
    await pool.start()

    cookies = load_cookies(LOGIN_STATE_PATH)
    writer = CsvWriter(OUTPUT_CSV)
    next_post_id = 1
    total_collected = 0

    try:
        async with build_client(cookies) as http_client:
            for task in SCRAPE_PLAN:
                try:
                    collected = await process_task(pool, http_client, task, writer, next_post_id)
                except SessionBlockedError as exc:
                    logger.error(
                        "检测到账号/会话被限制（%s），停止采集，已保存的 %d 条数据不受影响。"
                        "建议：1) 重新运行 save_login_state.py 刷新登录态 "
                        "2) 如果刷新后立刻又被限制，说明账号被风控，需等待冷却或更换账号",
                        exc,
                        total_collected,
                    )
                    break
                except Exception as exc:  # noqa: BLE001
                    logger.error("[%s] 任务执行异常，跳过该关键词：%s", task.keyword, exc)
                    collected = 0
                next_post_id += collected
                total_collected += collected
                logger.info("累计采集：%d 条", total_collected)
    finally:
        await pool.close()

    logger.info("全部任务完成，共采集 %d 条，写入 %s", total_collected, OUTPUT_CSV)


if __name__ == "__main__":
    asyncio.run(run())
