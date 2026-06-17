"""
单元测试 - utils/calculator.py 加权计算器

覆盖场景:
  - 标准合法分数计算
  - 非法分数自动修正
  - 缺失维度默认最低分
  - 权重之和为 1.0，总分区间验证
"""

import pytest
from utils.calculator import calculate_weighted_score, _validate_scores
from models.schema import WEIGHTS, DIMENSION_NAMES


# ==================== 辅助数据 ====================

ALL_TENS = {k: 10 for k in WEIGHTS}        # 所有维度满分
ALL_TWOS = {k: 2 for k in WEIGHTS}         # 所有维度最低分
MIXED_SCORES = {
    "vividness_emotion": 10,
    "vividness_setting": 6,
    "vulnerability": 6,
    "cognition": 6,
    "tone": 8,
    "volume": 6,
    "resolution": 6,
    "development": 6,
    "emo_shift": 6,
}


# ==================== 权重基础校验 ====================

class TestWeights:
    def test_weights_sum_to_one(self):
        """所有维度权重之和应精确等于 1.0"""
        total = sum(WEIGHTS.values())
        assert abs(total - 1.0) < 1e-9, f"权重之和为 {total}，不等于 1.0"

    def test_all_dimensions_have_weights(self):
        """所有维度名称必须在权重表中"""
        for key in DIMENSION_NAMES:
            assert key in WEIGHTS, f"维度 '{key}' 没有对应权重"


# ==================== 计算结果校验 ====================

class TestCalculateWeightedScore:
    def test_all_max_scores(self):
        """所有维度满分时，总分应为 10.0"""
        total, process = calculate_weighted_score(ALL_TENS)
        assert total == 10.0, f"满分应为10，实际={total}"
        assert "10.0" in process

    def test_all_min_scores(self):
        """所有维度最低分时，总分应为 2.0"""
        total, process = calculate_weighted_score(ALL_TWOS)
        assert total == 2.0, f"最低分应为2，实际={total}"

    def test_score_in_valid_range(self):
        """总分应在 (0, 10] 区间内"""
        total, _ = calculate_weighted_score(MIXED_SCORES)
        assert 0 < total <= 10, f"总分超出区间: {total}"

    def test_calculation_process_format(self):
        """计算过程字符串应包含所有维度名称和最终总分"""
        total, process = calculate_weighted_score(MIXED_SCORES)
        for name in DIMENSION_NAMES.values():
            assert name in process, f"计算过程缺少维度: {name}"
        assert str(total) in process

    def test_returns_tuple(self):
        """返回值应为 (float, str) 元组"""
        result = calculate_weighted_score(MIXED_SCORES)
        assert isinstance(result, tuple) and len(result) == 2
        total, process = result
        assert isinstance(total, float)
        assert isinstance(process, str)

    def test_missing_dimensions_use_default(self):
        """缺失维度应使用默认最低分 2.0，不抛出异常"""
        partial = {"vividness_emotion": 10}   # 只有一个维度
        total, process = calculate_weighted_score(partial)
        # 10 * 0.5 + 2 * (0.5 权重的其他维度)
        assert 0 < total <= 10


# ==================== 分数校验逻辑 ====================

class TestValidateScores:
    def test_valid_scores_unchanged(self):
        """合法分数不应被修改"""
        scores = {"vividness_emotion": 6, "tone": 8}
        result = _validate_scores(scores)
        assert result["vividness_emotion"] == 6.0
        assert result["tone"] == 8.0

    def test_invalid_score_snaps_down(self):
        """非法分数应向下取整到最近合法值"""
        # vividness_emotion 合法值为 [2, 6, 10]
        scores = {"vividness_emotion": 7}   # 7 不合法，应降为 6
        result = _validate_scores(scores)
        assert result["vividness_emotion"] == 6.0

    def test_score_below_min_snaps_to_min(self):
        """低于合法值下界时，应取最小合法值"""
        scores = {"vividness_emotion": 1}   # 1 低于最小值 2
        result = _validate_scores(scores)
        assert result["vividness_emotion"] == 2.0

    def test_unknown_dimension_passes_through(self):
        """未知维度的分数直接透传，不校验"""
        scores = {"unknown_dim": 7.5}
        result = _validate_scores(scores)
        assert result["unknown_dim"] == 7.5

    def test_all_valid_score_sets(self):
        """对每个维度的所有合法分数值逐一验证"""
        from models.schema import VALID_SCORES
        for key, valid_set in VALID_SCORES.items():
            for v in valid_set:
                result = _validate_scores({key: v})
                assert result[key] == float(v), f"{key}={v} 应保持不变"
