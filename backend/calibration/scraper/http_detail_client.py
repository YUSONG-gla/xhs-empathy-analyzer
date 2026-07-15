"""
详情页内容获取：用 httpx 直接请求笔记详情页，从 window.__INITIAL_STATE__ 提取正文。

从页面的 window.__INITIAL_STATE__ JSON 中提取笔记的完整标题和正文。
这是小红书 SSR 页面的标准数据结构，包含完整的笔记内容。
"""

from __future__ import annotations

import html
import json
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx
import json5

logger = logging.getLogger("scraper.http_detail")

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# 匹配 window.__INITIAL_STATE__ = {...}
INITIAL_STATE_PATTERN = re.compile(r'window\.__INITIAL_STATE__\s*=\s*({.*?})\s*</script>', re.S)


class SessionBlockedError(Exception):
    """
    检测到详情页请求被重定向到登录页，说明当前 cookie 已失效/账号被限制。
    这是账号级问题，继续用同一份 cookie 发请求只会全部失败，
    调用方应该捕获这个异常并提前终止整个采集流程，而不是逐条跳过浪费时间。
    """


@dataclass
class NoteDetail:
    note_id: str
    title: str
    content: str


def load_cookies(path: str) -> dict[str, str]:
    """
    把 Playwright storage_state.json 里的 cookies 转成 httpx 可用的 dict。

    详情页接口不强制要求登录态（参考脚本验证过未登录也能拿到正文），
    但带上登录态通常能看到更完整的内容，文件不存在时返回空 dict 即可，
    不阻塞整体流程。
    """
    state_path = Path(path)
    if not state_path.exists():
        logger.warning("找不到登录态文件：%s，将以未登录身份请求详情页", path)
        return {}

    with open(state_path, encoding="utf-8") as f:
        state = json.load(f)

    return {c["name"]: c["value"] for c in state.get("cookies", [])}


def build_client(cookies: dict[str, str]) -> httpx.AsyncClient:
    headers = {
        "User-Agent": DEFAULT_UA,
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://www.xiaohongshu.com/",
    }
    return httpx.AsyncClient(
        cookies=cookies, headers=headers, follow_redirects=True, timeout=20
    )


async def fetch_detail(
    client: httpx.AsyncClient, note_id: str, detail_url: str
) -> NoteDetail | None:
    """
    请求笔记详情页，从 window.__INITIAL_STATE__ 提取标题和正文。
    失败（404 重定向 / 请求异常 / 提取不到正文）统一返回 None。
    """
    try:
        resp = await client.get(detail_url)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[%s] 请求详情页异常：%s", note_id, exc)
        return None

    if "/404" in str(resp.url):
        logger.warning("[%s] 笔记不存在或无法查看（重定向到 404）", note_id)
        return None

    if "/login" in str(resp.url):
        # 这不是单条笔记的问题，是整个会话失效了，继续跑只会一条接一条全部失败
        raise SessionBlockedError(
            f"详情页请求被重定向到登录页（{resp.url}），cookie 已失效或账号被限制"
        )

    if resp.status_code != 200:
        logger.warning("[%s] 详情页返回非 200 状态码：%s", note_id, resp.status_code)
        return None

    page_html = resp.text

    # 提取 window.__INITIAL_STATE__
    match = INITIAL_STATE_PATTERN.search(page_html)
    if not match:
        logger.warning("[%s] 未找到 window.__INITIAL_STATE__", note_id)
        return None

    state_json = match.group(1)

    try:
        # 处理 undefined 值（JavaScript 特有，JSON 不支持）
        state_json = re.sub(r':\s*undefined\b', ': null', state_json)
        state = json5.loads(state_json)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[%s] 解析 __INITIAL_STATE__ 失败：%s", note_id, exc)
        return None

    # 从 state.note.noteDetailMap[note_id].note 提取数据
    try:
        note_data = state["note"]["noteDetailMap"][note_id]["note"]
        title = note_data.get("title", "").strip()
        content = note_data.get("desc", "").strip()
    except (KeyError, TypeError) as exc:
        logger.warning("[%s] noteDetailMap 中未找到笔记数据：%s", note_id, exc)
        return None

    if not content:
        logger.warning("[%s] 正文为空", note_id)
        return None

    return NoteDetail(note_id=note_id, title=title, content=content)
