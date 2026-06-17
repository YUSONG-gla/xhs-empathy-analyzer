"""
评分主流程编排器 - 协调 Prompt 构建 → LLM 调用 → 解析 → 计算的全流程
"""

import asyncio
import logging
import time

from models.schema import (
    ScoreResponse, DimensionScore, DIMENSION_NAMES, ScoreRequest
)
from services.prompt_builder import build_prompt, get_system_prompt
from services.llm_service import create_llm_service, BaseLLMService
from utils.parser import parse_llm_response
from utils.calculator import calculate_weighted_score
from calibration.calibrator import calibrate, is_enabled as calibration_enabled

logger = logging.getLogger(__name__)


async def score_text(request: ScoreRequest) -> ScoreResponse:
    """
    对文本进行完整的共情力评分
    
    流程: 构建Prompt → 调用LLM → 解析JSON → 加权计算 → 返回结果
    """
    start_time = time.time()

    try:
        # 1. 创建 LLM 服务实例
        llm: BaseLLMService = create_llm_service()
        model_name = llm.model_name

        # 2. 构建 Prompt
        system_prompt = get_system_prompt()
        user_prompt = build_prompt(request.text)

        # 3. 调用 LLM（含重试机制）
        raw_response = await _call_with_retry(llm, system_prompt, user_prompt)

        # 4. 解析 LLM 输出
        parsed_data = parse_llm_response(raw_response)
        
        if parsed_data is None:
            raise ValueError("LLM 输出解析失败，无法提取有效评分数据")

        # 5. 提取 LLM 原始维度分数
        scores_dict, dimensions_list = _extract_dimensions(parsed_data)

        # 5.5 ML 校准层：将 LLM 分对齐至人工标注（校准器未加载时透明跳过）
        calibrated_scores = calibrate(scores_dict)
        if calibration_enabled() and calibrated_scores is not scores_dict:
            # 同步更新 dimensions_list 中的 score 字段，使 API 响应展示校准后分数
            calibrated_map = calibrated_scores
            dimensions_list = [
                d.model_copy(update={"score": calibrated_map.get(d.key, d.score)})
                for d in dimensions_list
            ]

        # 6. 加权求和（使用校准后分数）
        total_score, calculation_process = calculate_weighted_score(calibrated_scores)

        # 6. 提取综合评价
        evaluation = parsed_data.get(
            "evaluation", 
            "未能从 LLM 响应中提取到综合评价。"
        )

        elapsed = round(time.time() - start_time, 2)
        logger.info(
            f"评分完成 | 总分={total_score} | 耗时={elapsed}s | "
            f"模型={model_name} | 校准={'已启用' if calibration_enabled() else '未启用'}"
        )

        return ScoreResponse(
            success=True,
            dimensions=dimensions_list,
            total_score=total_score,
            calculation_process=calculation_process,
            evaluation=evaluation,
            model_used=model_name,
        )

    except Exception as e:
        logger.error(f"评分过程出错: {e}", exc_info=True)
        raise


async def _call_with_retry(llm: BaseLLMService, system_prompt: str,
                            user_prompt: str) -> str:
    """带重试机制的 LLM 调用"""
    from config.settings import get_settings
    settings = get_settings()
    max_retries = settings.llm_max_retries

    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            return await llm.call(system_prompt, user_prompt)
        except Exception as e:
            last_error = e
            logger.warning(f"LLM 调用第 {attempt}/{max_retries} 次失败: {e}")
            if attempt < max_retries:
                await asyncio.sleep(1 * attempt)  # 指数退避

    raise RuntimeError(f"LLM 调用 {max_retries} 次均失败: {last_error}")


def _extract_dimensions(parsed_data: dict) -> tuple[dict[str, float], list[DimensionScore]]:
    """
    从解析后的数据中提取各维度分数
    
    Returns:
        (scores_dict, dimensions_list): 分数字典和 DimensionScore 列表
    """
    scores_dict = {}
    dimensions_list = []

    scores_raw = parsed_data.get("scores", parsed_data)

    for key, name in DIMENSION_NAMES.items():
        if key in scores_raw:
            item = scores_raw[key]
            if isinstance(item, dict):
                score = float(item.get("score", 2))
                reason = item.get("reason", f"{name} 评分完成")
            else:
                score = float(item)
                reason = f"{name} 自动提取分数"

            scores_dict[key] = score
            dimensions_list.append(DimensionScore(
                name=name, key=key, score=score, reason=reason
            ))
        else:
            # 缺失的维度给最低分
            default_score = 2.0
            scores_dict[key] = default_score
            dimensions_list.append(DimensionScore(
                name=name, key=key, score=default_score,
                reason=f"{name}: 该维度未在 LLM 评分中返回，默认赋值",
            ))

    return scores_dict, dimensions_list
