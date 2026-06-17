"""
加权计算器 - 纯函数模块，无副作用，便于单元测试

输入: 9 个维度的 {key: score} 字典
输出: (total_score, calculation_process)
"""

from models.schema import WEIGHTS, DIMENSION_NAMES, VALID_SCORES


def calculate_weighted_score(scores: dict[str, float]) -> tuple[float, str]:
    """
    按权重公式计算共情度总分
    
    Args:
        scores: 9 个维度的分数字典, 如 {"vividness_emotion": 6.0, ...}
    
    Returns:
        (total_score, calculation_process): 总分和计算过程字符串
    """
    # 校验并修正非法分数
    validated_scores = _validate_scores(scores)

    parts = []
    total = 0.0

    for key, weight in WEIGHTS.items():
        score = validated_scores.get(key, 2.0)  # 缺失维度默认最低分
        weighted = round(score * weight, 3)
        name = DIMENSION_NAMES.get(key, key)
        part = f"{name}({score}) * {weight} = {weighted}"
        parts.append(part)
        total += weighted

    total_rounded = round(total, 2)
    process = " + ".join(parts) + f" = {total_rounded}"

    return total_rounded, process


def _validate_scores(scores: dict[str, float]) -> dict[str, float]:
    """
    校验各维度分数是否在合法值集合内，
    非法值取最接近的合法值（向下取整到合法区间）
    """
    validated = {}
    for key, value in scores.items():
        valid_set = VALID_SCORES.get(key)
        if valid_set is None:
            validated[key] = float(value)
            continue

        if value in valid_set:
            validated[key] = float(value)
        else:
            # 取合法集合中小于等于当前值的最大值
            lower = [v for v in valid_set if v <= value]
            validated[key] = float(max(lower)) if lower else min(valid_set)

    return validated
