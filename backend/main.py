"""
HEART 共情力评分系统 - FastAPI 应用入口

启动方式:
    uvicorn main:app --reload --port 8000

API 文档:
    http://localhost:8000/docs (Swagger UI)
    http://localhost:8000/redoc (ReDoc)
"""

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.routes import router
from api.dependencies import RequestIDMiddleware
from config.settings import get_settings
from calibration.calibrator import load_calibrators

# ==================== 日志配置 ====================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ==================== 应用生命周期 ====================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理 - 启动时加载配置，关闭时清理资源"""
    settings = get_settings()
    
    logger.info("=" * 50)
    logger.info("HEART 共情力评分系统 启动中...")
    logger.info(f"  LLM 提供商: {settings.llm_provider}")
    logger.info(f"  模型名称: {settings.openai_model if settings.llm_provider == 'openai' else settings.gemini_model}")
    logger.info(f"  超时设置: {settings.llm_timeout}s")

    # 预加载 ML 校准器（文件不存在时自动降级，不阻断启动）
    calibration_ok = load_calibrators()
    logger.info(f"  ML校准层: {'已启用' if calibration_ok else '未启用（未训练）'}")
    logger.info("=" * 50)

    yield  # 应用运行中
    
    logger.info("HEART 共情力评分系统 已关闭")


# ==================== 创建 FastAPI 实例 ====================

app = FastAPI(
    title="HEART 共情力评分系统",
    description="基于 LLM 的文本共情力量化评估 API 服务。支持 OpenAI 和 Gemini 双提供商。",
    version="1.0.0-MVP",
    lifespan=lifespan,
)

# 注册请求 ID 中间件（顺序：先注册后执行）
app.add_middleware(RequestIDMiddleware)

# 注册 CORS（允许前端跨域调用）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],       # MVP 阶段全开放
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(router)


# ==================== 全局异常处理 ====================

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """兜底全局异常处理"""
    logger.error(f"未捕获的异常: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error": "服务器内部错误",
            "detail": str(exc) if __debug__ else None,
        },
    )


# ==================== 健康检查 ====================

@app.get("/health", tags=["系统"])
async def health_check():
    return {"status": "ok", "service": "heart-scorer", "version": "1.0.0-MVP"}
