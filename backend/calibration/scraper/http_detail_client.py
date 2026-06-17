"""
详情页内容获取：用 httpx 直接请求笔记详情页，正则提取 meta 标签拿正文。

简化自一个已验证可用的参考脚本：直接请求详情页 HTML，
从 `<meta name="og:title">` 和 `<meta name="description">` 里提取标题和正文，
不需要解析复杂的 __INITIAL_STATE__ JSON，也不强制要求登录态。
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

logger = logging.getLogger("scraper.http_detail")

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

TITLE_PATTERN = re.compile(r'<meta name="og:title" content="(.*?)"\s*/?>', re.S)
DESC_PATTERN = re.compile(r'<meta name="description" content="(.*?)"\s*/?>', re.S)


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
    请求笔记详情页，正则提取 og:title / description 两个 meta 标签。
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

    title_match = TITLE_PATTERN.search(page_html)
    desc_match = DESC_PATTERN.search(page_html)

    title = html.unescape(title_match.group(1)).strip() if title_match else ""
    # og:title 末尾固定带 " - 小红书" 网站名后缀，去掉
    title = re.sub(r"\s*-\s*小红书\s*$", "", title)
    content = html.unescape(desc_match.group(1)).strip() if desc_match else ""

    if not content:
        logger.warning("[%s] 未能从详情页提取到正文（description meta 标签）", note_id)
        return None

    return NoteDetail(note_id=note_id, title=title, content=content)
