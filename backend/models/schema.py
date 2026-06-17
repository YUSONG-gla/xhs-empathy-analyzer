from pydantic import BaseModel, Field
from typing import Optional


# ==================== 维度定义 ====================

# 各维度的合法分数集合
VALID_SCORES = {
    "vividness_emotion": [2, 6, 10],
    "vividness_setting": [2, 6, 10],
    "vulnerability": [2, 6, 10],
    "cognition": [2, 6, 10],
    "tone": [2, 4, 6, 8, 10],
    "volume": [2, 4, 6, 8, 10],
    "resolution": [2, 4, 6, 8, 10],
    "development": [2, 4, 6, 8, 10],
    "emo_shift": [2, 4, 6, 8, 10],
}

# 维度中英文名称映射
DIMENSION_NAMES = {
    "vividness_emotion": "情感生动性",
    "vividness_setting": "环境生动性",
    "vulnerability": "角色脆弱性",
    "cognition": "认知表述丰富度",
    "tone": "语气情绪",
    "volume": "情节体量",
    "resolution": "故事矛盾解决程度",
    "development": "角色发展程度",
    "emo_shift": "情绪转变程度",
}

# 加权权重
WEIGHTS = {
    "vividness_emotion": 0.50,
    "vividness_setting": 0.15,
    "vulnerability": 0.10,
    "cognition": 0.08,
    "tone": 0.05,
    "volume": 0.04,
    "resolution": 0.03,
    "development": 0.03,
    "emo_shift": 0.02,
}


# ==================== Pydantic 模型 ====================

class DimensionScore(BaseModel):
    """单个维度评分结果"""
    name: str = Field(..., description="维度中文名")
    key: str = Field(..., description="维度英文标识")
    score: float = Field(..., ge=0, le=10, description="得分")
    reason: str = Field(..., description="判定理由（1-2句话）")


class ScoreRequest(BaseModel):
    """评分请求体"""
    text: str = Field(
        ...,
        min_length=10,
        max_length=10000,
        description="待评分的文本内容，长度 10~10000 字符",
    )


class ScoreResponse(BaseModel):
    """评分响应体"""
    success: bool = Field(..., description="是否成功完成评分")
    dimensions: list[DimensionScore] = Field(..., description="9 个维度评分详情列表")
    total_score: float = Field(..., ge=0, le=10, description="加权共情度总分 (0, 10]")
    calculation_process: str = Field(..., description="公式计算过程展示")
    evaluation: str = Field(..., description="综合评价")
    model_used: str = Field(..., description="使用的 LLM 模型名称")


class ErrorResponse(BaseModel):
    """错误响应"""
    success: bool = False
    error: str = Field(..., description="错误信息")
    detail: Optional[str] = None
