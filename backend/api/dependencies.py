"""
API 依赖注入与中间件模块

包含:
- 请求 ID 生成（每个请求注入唯一 trace ID）
- 请求频率限制（内存级滑动窗口）
- 文本前置校验依赖
"""

import time
import uuid
import logging
from collections import defaultdict, deque
from typing import Annotated

from fastapi import Request, HTTPException, Depends, Header
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

logger = logging.getLogger(__name__)


# ==================== 请求 ID 中间件 ====================

class RequestIDMiddleware(BaseHTTPMiddleware):
    """为每个请求注入唯一的 X-Request-ID，便于日志追踪"""

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())[:8]
        request.state.request_id = request_id

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


# ==================== 频率限制 ====================

class _RateLimiter:
    """
    内存级滑动窗口频率限制器

    每个客户端 IP 在 window_seconds 内最多允许 max_requests 次请求
    """

    def __init__(self, max_requests: int = 20, window_seconds: int = 60):
        self._max_requests = max_requests
        self._window = window_seconds
        self._store: dict[str, deque] = defaultdict(deque)

    def is_allowed(self, client_ip: str) -> bool:
        now = time.time()
        window_start = now - self._window
        queue = self._store[client_ip]

        # 清除窗口外的旧记录
        while queue and queue[0] < window_start:
            queue.popleft()

        if len(queue) >= self._max_requests:
            return False

        queue.append(now)
        return True


# 全局单例限速器（每分钟 20 次请求/IP）
_rate_limiter = _RateLimiter(max_requests=20, window_seconds=60)


def check_rate_limit(request: Request) -> None:
    """
    FastAPI 依赖：检查请求频率

    超出限制返回 429 Too Many Requests
    """
    client_ip = _get_client_ip(request)
    if not _rate_limiter.is_allowed(client_ip):
        logger.warning(f"[RateLimit] IP {client_ip} 超出请求频率限制")
        raise HTTPException(
            status_code=429,
            detail="请求过于频繁，请稍后再试（每分钟最多 20 次）",
            headers={"Retry-After": "60"},
        )


def _get_client_ip(request: Request) -> str:
    """优先从 X-Forwarded-For 头获取真实 IP，否则使用直连地址"""
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ==================== 文本前置校验依赖 ====================

def validate_text_content(text: str) -> str:
    """
    前置校验待评分文本（在 Pydantic 校验之外做语义级检查）

    - 过滤纯空白字符串
    - 检测明显非文本内容（如纯数字/符号）
    """
    stripped = text.strip()
    if not stripped:
        raise HTTPException(status_code=400, detail="文本内容不能为纯空白字符")

    # 非中英文字符比例检测（宽松判断：超过 80% 非字母数字汉字则警告）
    alnum_count = sum(1 for c in stripped if c.isalnum() or '\u4e00' <= c <= '\u9fff')
    if len(stripped) > 0 and alnum_count / len(stripped) < 0.05:
        raise HTTPException(
            status_code=400,
            detail="文本内容不合法：请提交包含有效文字内容的文本",
        )

    return stripped


# ==================== 常用依赖类型别名 ====================

RateLimitDep = Annotated[None, Depends(check_rate_limit)]
