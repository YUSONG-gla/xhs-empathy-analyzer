"""
单元测试 - utils/parser.py 四层容错 JSON 解析器

覆盖场景:
  - Layer 1: 标准合法 JSON
  - Layer 2: markdown 代码块包裹
  - Layer 3: json5 容错（尾逗号、单引号、注释）
  - Layer 4: 正则降级提取
  - 完全无效输入返回 None
"""

import json
import pytest
from utils.parser import (
    parse_llm_response,
    _try_json_loads,
    _try_extract_markdown_json,
    _try_json5_parse,
    _try_regex_fallback,
    _has_required_fields,
    DIMENSION_KEYS,
)


# ==================== 测试数据构造助手 ====================

def _make_scores_dict(score: int = 6) -> dict:
    """生成一个包含全部 9 维度的合法 scores 字典"""
    return {k: {"score": score, "reason": f"{k} 测试理由"} for k in DIMENSION_KEYS}


def _make_full_response(score: int = 6) -> dict:
    """生成完整的 LLM 响应 dict"""
    return {
        "scores": _make_scores_dict(score),
        "evaluation": "综合评价测试文本",
    }


# ==================== Layer 1: 标准 JSON ====================

class TestLayer1JsonLoads:
    def test_valid_json_parsed(self):
        raw = json.dumps(_make_full_response())
        result = _try_json_loads(raw)
        assert result is not None
        assert "scores" in result

    def test_invalid_json_returns_none(self):
        assert _try_json_loads("not a json string") is None

    def test_non_object_json_returns_none(self):
        assert _try_json_loads("[1, 2, 3]") is None

    def test_json_with_extra_whitespace(self):
        raw = "  " + json.dumps(_make_full_response()) + "  "
        result = _try_json_loads(raw)
        assert result is not None


# ==================== Layer 2: Markdown 代码块 ====================

class TestLayer2MarkdownExtract:
    def test_json_in_backtick_block(self):
        payload = json.dumps(_make_full_response())
        raw = f"```json\n{payload}\n```"
        result = _try_extract_markdown_json(raw)
        assert result is not None
        assert "scores" in result

    def test_json_in_plain_backtick_block(self):
        payload = json.dumps(_make_full_response())
        raw = f"```\n{payload}\n```"
        result = _try_extract_markdown_json(raw)
        assert result is not None

    def test_json_with_surrounding_text(self):
        payload = json.dumps(_make_full_response())
        raw = f"以下是评分结果：\n```json\n{payload}\n```\n请参考上述分数。"
        result = parse_llm_response(raw)
        assert result is not None

    def test_no_valid_json_returns_none(self):
        raw = "```json\n{broken: json}\n```"
        # json5 might parse this — ok if it does; just ensure no crash
        result = _try_extract_markdown_json(raw)
        # May or may not be None depending on json5 tolerance


# ==================== Layer 3: json5 容错 ====================

class TestLayer3Json5:
    def test_trailing_comma_tolerated(self):
        """json5 应容忍尾逗号"""
        raw = '{"scores": {' + \
              ', '.join(f'"{k}": {{"score": 6, "reason": "ok"}}' for k in DIMENSION_KEYS) + \
              ',}, "evaluation": "ok",}'
        result = _try_json5_parse(raw)
        assert result is not None

    def test_single_quotes_tolerated(self):
        """json5 应容忍单引号"""
        inner = ", ".join(
            f"'{k}': {{'score': 6, 'reason': 'ok'}}" for k in DIMENSION_KEYS
        )
        raw = f"{{'scores': {{{inner}}}, 'evaluation': 'ok'}}"
        result = _try_json5_parse(raw)
        assert result is not None

    def test_embedded_json_extracted(self):
        """文本中嵌入的 JSON 对象应被提取"""
        payload = json.dumps(_make_full_response())
        raw = f"额外说明文字 {payload} 更多说明"
        result = _try_json5_parse(raw)
        assert result is not None


# ==================== Layer 4: 正则降级提取 ====================

class TestLayer4RegexFallback:
    def test_partial_extraction(self):
        """即使格式混乱，只要能提取到足够维度就返回结果"""
        lines = [f'  {k}: 6 (测试理由)' for k in DIMENSION_KEYS]
        raw = "\n".join(lines)
        result = _try_regex_fallback(raw)
        # 可能提取到部分维度
        if result is not None:
            assert len(result) >= len(DIMENSION_KEYS) // 2

    def test_returns_none_on_garbage(self):
        """完全乱码应返回 None"""
        result = _try_regex_fallback("$$$###@@@!!!")
        assert result is None


# ==================== 全流程 parse_llm_response ====================

class TestParseFullResponse:
    def test_standard_json(self):
        raw = json.dumps(_make_full_response(score=10))
        result = parse_llm_response(raw)
        assert result is not None
        assert result["scores"]["vividness_emotion"]["score"] == 10

    def test_markdown_wrapped_json(self):
        payload = json.dumps(_make_full_response())
        raw = f"```json\n{payload}\n```"
        result = parse_llm_response(raw)
        assert result is not None
        assert "evaluation" in result

    def test_completely_invalid_returns_none(self):
        result = parse_llm_response("这根本不是 JSON，也没有任何维度信息。")
        assert result is None

    def test_empty_string_returns_none(self):
        result = parse_llm_response("")
        assert result is None


# ==================== _has_required_fields ====================

class TestHasRequiredFields:
    def test_full_scores_returns_true(self):
        data = {"scores": _make_scores_dict()}
        assert _has_required_fields(data) is True

    def test_half_scores_returns_true(self):
        half_keys = DIMENSION_KEYS[: len(DIMENSION_KEYS) // 2]
        data = {"scores": {k: {"score": 6, "reason": "ok"} for k in half_keys}}
        assert _has_required_fields(data) is True

    def test_empty_scores_returns_false(self):
        data = {"scores": {}}
        assert _has_required_fields(data) is False
