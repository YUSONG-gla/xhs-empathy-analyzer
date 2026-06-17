"""
API 路由层 - 定义评分接口，处理请求校验与响应封装
"""

import logging
from fastapi import APIRouter, HTTPException, Depends

from models.schema import ScoreRequest, ScoreResponse, ErrorResponse
from services.scorer import score_text
from api.dependencies import check_rate_limit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["评分"])


@router.post(
    "/score",
    response_model=ScoreResponse,
    dependencies=[Depends(check_rate_limit)],
    responses={
        400: {"model": ErrorResponse, "description": "请求参数错误"},
        429: {"model": ErrorResponse, "description": "请求频率超限"},
        500: {"model": ErrorResponse, "description": "服务内部错误"},
        502: {"model": ErrorResponse, "description": "LLM 调用失败"},
    },
    summary="文本共情力评分",
    description="提交文本，按 HEART 框架从 9 个维度进行共情力量化打分并返回加权总分",
)
async def score_text_endpoint(request: ScoreRequest) -> ScoreResponse:
    """POST /api/score - 文本共情力评分接口"""
    try:
        result = await score_text(request)
        return result
    except ValueError as e:
        logger.warning(f"评分数据解析错误: {e}")
        raise HTTPException(status_code=502, detail=str(e))
    except RuntimeError as e:
        logger.error(f"LLM 服务调用失败: {e}")
        raise HTTPException(status_code=502, detail=f"LLM 调用异常: {e}")
    except Exception as e:
        logger.error(f"评分过程未知错误: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"服务器内部错误: {e}")
