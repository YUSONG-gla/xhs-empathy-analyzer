"""
ML 校准层 - 运行时推理模块

职责：
  1. 启动时从 calibrators.pkl 加载预训练的保序回归模型
  2. calibrate() 将 LLM 原始分映射到与人工标注对齐的分数
  3. 若 pkl 不存在（未训练），透明降级为直接返回原始分

接入位置（scorer.py）：
  Step 5: _extract_dimensions() → scores_dict   (LLM 原始分)
  Step 5.5: calibrate(scores_dict)              ← 本模块
  Step 6: calculate_weighted_score(...)
"""

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 默认 pkl 路径（相对于 backend/ 运行目录）
_DEFAULT_PKL = Path(__file__).parent / "artifacts" / "calibrators.pkl"

# 全局缓存（进程内单例）
_calibrators: Optional[dict] = None
_calibration_enabled: bool = False


def load_calibrators(path: str | Path = _DEFAULT_PKL) -> bool:
    """
    加载预训练校准器。在 main.py lifespan 中调用一次。

    Returns:
        True  - 加载成功，校准已启用
        False - 文件不存在，降级为直接使用 LLM 原始分
    """
    global _calibrators, _calibration_enabled

    path = Path(path)
    if not path.exists():
        logger.warning(
            f"[Calibrator] 校准器文件不存在: {path}\n"
            "  → 系统将直接使用 LLM 原始分（未校准模式）\n"
            "  → 如需启用校准，请先运行: python calibration/batch_score.py，"
            "再运行 python calibration/trainer.py"
        )
        _calibration_enabled = False
        return False

    try:
        import joblib
        _calibrators = joblib.load(path)
        _calibration_enabled = True
        logger.info(f"[Calibrator] 校准器加载成功: {len(_calibrators)} 个维度 | 路径={path}")
        return True
    except Exception as e:
        logger.error(f"[Calibrator] 加载失败: {e}，降级为未校准模式")
        _calibration_enabled = False
        return False


def calibrate(scores_dict: dict[str, float]) -> dict[str, float]:
    """
    将 LLM 原始 9 维分数校准为与人工标注对齐的分数。

    输入输出格式与 scores_dict 完全一致，对调用方透明。
    若校准器未加载，直接返回原始分（无任何副作用）。

    Args:
        scores_dict: {维度key: LLM原始分(2~10)} 字典

    Returns:
        校准后的 {维度key: 校准分(合法HEART值)} 字典
    """
    if not _calibration_enabled or _calibrators is None:
        return scores_dict

    from models.schema import VALID_SCORES

    calibrated: dict[str, float] = {}

    for dim, raw_score in scores_dict.items():
        if dim not in _calibrators:
            calibrated[dim] = raw_score
            continue

        # 1. 将 LLM 分从 [2, 10] 归一化到 [0, 1]
        normalized = (float(raw_score) - 2.0) / 8.0
        normalized = max(0.0, min(1.0, normalized))   # clip 防越界

        # 2. 保序回归预测（输出仍在 [0, 1]）
        cal_normalized = float(_calibrators[dim].predict([normalized])[0])
        cal_normalized = max(0.0, min(1.0, cal_normalized))

        # 3. 映射回 [2, 10] 后取最近合法值
        cal_raw = cal_normalized * 8.0 + 2.0
        legal_values = VALID_SCORES.get(dim, [2, 4, 6, 8, 10])
        calibrated[dim] = float(min(legal_values, key=lambda v: abs(v - cal_raw)))

    return calibrated


def is_enabled() -> bool:
    """返回校准是否已启用（用于日志和响应头）"""
    return _calibration_enabled
