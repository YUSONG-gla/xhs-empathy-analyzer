"""
搜索结果列表采集：通过 Playwright 打开搜索页，拦截浏览器自己发出的
搜索接口请求（该接口带 x-s / x-s-common 签名，httpx 无法直连模拟），
直接读取接口返回的原始 JSON，而不是等 DOM 渲染后用 CSS 选择器抓取。

关键点：URL 必须带上 `source=web_search_result_notes` 参数，
否则页面不会触发那个真正返回笔记列表的接口请求（已用可工作的参考脚本验证）。

这样做的好处：
1. 不依赖页面 DOM 结构/CSS class，页面改版也不容易失效
2. 拿到的是接口原始数据（笔记 ID、xsec_token、点赞数等精确字段）
3. 比等待 DOM 渲染更快、更稳定
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from urllib.parse import quote

from playwright.async_api import Page

from .config import SEARCH_API_URL_PATTERN

logger = logging.getLogger("scraper.search_capture")

# 必须带上 source 参数，否则不会触发搜索接口请求
SEARCH_URL = (
    "https://www.xiaohongshu.com/search_result"
    "?keyword={keyword}&source=web_search_result_notes"
)


@dataclass
class NoteSummary:
    """从搜索接口响应中提取的单条笔记摘要信息。"""

    note_id: str
    title: str
    xsec_token: str
    like_count: int

    def detail_url(self) -> str:
        return (
            f"https://www.xiaohongshu.com/explore/{self.note_id}"
            f"?xsec_token={self.xsec_token}&xsec_source=pc_search"
        )


def _safe_int(value) -> int:
    """接口里的数字常以字符串形式返回（如 "5093"），统一安全转换。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _parse_items(payload: dict) -> list[NoteSummary]:
    """
    从搜索接口的 JSON payload 中提取笔记列表。

    真实结构（已用实测响应验证）：
        payload = {
            "code": 0,
            "success": true,
            "data": {
                "has_more": true,
                "items": [
                    {
                        "id": "笔记ID",
                        "xsec_token": "...",
                        "model_type": "note",          # 也可能是 "rec_query"（相关搜索推荐，需过滤）
                        "note_card": {
                            "display_title": "...",     # 部分笔记可能为空字符串
                            "interact_info": {
                                "liked_count": "...",   # 字符串类型数字
                                ...
                            }
                        }
                    },
                    {
                        "id": "...#时间戳",
                        "model_type": "rec_query",       # 相关搜索推荐词条，没有 note_card，需跳过
                        "rec_query": {...}
                    },
                    ...
                ]
            }
        }
    """
    try:
        items = payload["data"]["items"]
    except (KeyError, TypeError):
        logger.warning(
            "搜索响应结构与预期不符，payload 顶层 key：%s", list(payload.keys())
        )
        return []

    notes: list[NoteSummary] = []
    for item in items:
        # 响应里会混入 model_type == "rec_query"（"相关搜索"推荐词条），
        # 这类条目没有 note_card，但 id/xsec_token 仍然存在，必须显式过滤掉。
        if item.get("model_type") != "note":
            continue

        note_card = item.get("note_card") or item.get("noteCard") or {}
        note_id = item.get("id") or item.get("note_id") or ""
        xsec_token = item.get("xsec_token") or note_card.get("xsec_token") or ""
        title = note_card.get("display_title") or note_card.get("title") or ""
        interact = note_card.get("interact_info") or note_card.get("interactInfo") or {}
        like_count = _safe_int(interact.get("liked_count") or interact.get("likedCount"))

        if not note_id or not xsec_token:
            continue

        notes.append(
            NoteSummary(
                note_id=note_id,
                title=title,
                xsec_token=xsec_token,
                like_count=like_count,
            )
        )

    return notes


async def capture_search_notes(
    page: Page, keyword: str, target_count: int, max_scroll_rounds: int = 8
) -> list[NoteSummary]:
    """
    直接跳转到带 source 参数的搜索结果 URL，通过监听网络响应捕获
    搜索接口返回的笔记列表；通过滚动触发"加载更多"累积更多结果。
    """
    collected: dict[str, NoteSummary] = {}  # note_id -> NoteSummary，自动去重
    response_event = asyncio.Event()

    async def on_response(response):
        if SEARCH_API_URL_PATTERN not in response.url:
            return
        try:
            payload = await response.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning("解析搜索响应 JSON 失败：%s", exc)
            return

        notes = _parse_items(payload)
        for note in notes:
            collected[note.note_id] = note
        logger.info(
            "[%s] 本次响应解析到 %d 条笔记，累计 %d 条", keyword, len(notes), len(collected)
        )
        response_event.set()

    page.on("response", on_response)

    try:
        url = SEARCH_URL.format(keyword=quote(keyword))

        goto_ok = False
        for attempt in range(1, 4):
            try:
                await page.goto(url, timeout=60000, wait_until="domcontentloaded")
                goto_ok = True
                break
            except Exception as exc:  # noqa: BLE001
                logger.warning("[%s] 打开搜索页失败（第 %d/3 次）：%s", keyword, attempt, exc)
                await asyncio.sleep(5 * attempt)
        if not goto_ok:
            logger.error("[%s] 多次重试后仍无法打开搜索页，放弃该关键词", keyword)
            return []

        # 等待首次搜索响应到达，给足时间（接口可能因为网络/风控延迟较久）
        try:
            await asyncio.wait_for(response_event.wait(), timeout=120)  # 2 分钟
        except asyncio.TimeoutError:
            logger.warning("[%s] 等待首次搜索响应超时（已等2分钟）", keyword)

        # 滚动触发分页加载，每次滚动后等待新响应
        rounds = 0
        while len(collected) < target_count and rounds < max_scroll_rounds:
            response_event.clear()
            await page.mouse.wheel(0, random.randint(1000, 1800))
            try:
                await asyncio.wait_for(response_event.wait(), timeout=8)
            except asyncio.TimeoutError:
                # 没有新响应，可能已经到底了，再多滚几次仍没有就放弃
                pass
            await asyncio.sleep(random.uniform(1.0, 2.0))
            rounds += 1

    finally:
        page.remove_listener("response", on_response)

    result = list(collected.values())[:target_count]
    logger.info("[%s] 列表采集完成，共 %d 条（目标 %d 条）", keyword, len(result), target_count)
    return result
