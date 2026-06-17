"""
LLM 输出 JSON 解析器（四层容错策略）

核心难点：LLM 返回的 JSON 经常出现格式错误：
  - 被 markdown ```json ... ``` 包裹
  - 字段缺失或类型错误
  - 非标准格式（尾逗号、单引号、注释等）
  - 混合了额外文字说明
"""

import json
import re
import json5
import logging

logger = logging.getLogger(__name__)

# 维度 key 列表，用于降级提取
DIMENSION_KEYS = [
    "vividness_emotion", "vividness_setting", "vulnerability",
    "cognition", "tone", "volume", "resolution",
    "development", "emo_shift",
]


def parse_llm_response(raw_text: str) -> dict | None:
    """
    四层容错 JSON 解析策略
    
    Layer 1: 标准 json.loads
    Layer 2: 正则从 markdown 代码块中提取 JSON 后解析
    Layer 3: json5 容错解析（允许尾逗号、单引号、注释等）
    Layer 4: 正则逐字段降级提取（兜底方案）
    
    Returns:
        解析成功返回 dict, 全部失败返回 None
    """
    # 尝试四层策略
    result = _try_json_loads(raw_text)
    if result:
        return result

    result = _try_extract_markdown_json(raw_text)
    if result:
        return result

    result = _try_json5_parse(raw_text)
    if result:
        return result

    result = _try_regex_fallback(raw_text)
    if result:
        return result

    logger.error(f"全部四层解析均失败。原始文本前200字符:\n{raw_text[:200]}")
    return None


def _try_json_loads(text: str) -> dict | None:
    """Layer 1: 标准 JSON 解析"""
    text = text.strip()
    if not (text.startswith("{") and text.endswith("}")):
        return None
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, ValueError):
        pass
    return None


def _try_extract_markdown_json(text: str) -> dict | None:
    """Layer 2: 从 markdown 代码块中提取 JSON"""
    # 匹配 ```json ... ``` 或 ``` ... ```
    patterns = [
        r'```(?:json)?\s*\n?(.*?)\n?```',
        r'\{[^{}]*"scores"[^{}]*\}',  # 直接匹配包含 scores 的 JSON 片段
    ]
    for pattern in patterns:
        matches = re.findall(pattern, text, re.DOTALL)
        for match in matches:
            match = match.strip()
            if match.startswith("{"):
                try:
                    data = json.loads(match)
                    if isinstance(data, dict) and _has_required_fields(data):
                        return data
                except (json.JSONDecodeError, ValueError):
                    pass
            # 也尝试 json5
            try:
                data = json5.loads(match)
                if isinstance(data, dict) and _has_required_fields(data):
                    return data
            except Exception:
                pass
    return None


def _try_json5_parse(text: str) -> dict | None:
    """Layer 3: json5 容错解析"""
    text = text.strip()
    try:
        # 先尝试直接解析整个文本
        data = json5.loads(text)
        if isinstance(data, dict) and _has_required_fields(data):
            return data
    except Exception:
        pass

    # 再尝试从文本中找到最外层 { } 内容
    brace_match = re.search(r'\{.*\}', text, re.DOTALL)
    if brace_match:
        try:
            data = json5.loads(brace_match.group())
            if isinstance(data, dict) and _has_required_fields(data):
                return data
        except Exception:
            pass
    return None


def _try_regex_fallback(text: str) -> dict | None:
    """Layer 4: 正则逐字段降级提取（兜底）"""
    result = {}
    
    for key in DIMENSION_KEYS:
        # 尝试多种模式匹配维度分数和理由
        patterns = [
            # 模式1: "key": score 或 'key': score
            rf'(?:["\']?{key}["\']?)\s*[:：]\s*(\d+(?:\.\d+)?)',
            # 模式2: 中文名：score
            rf'{re.escape(key)}[\s]*(\d+(?:\.\d+)?)',
        ]
        
        for pattern in patterns:
            m = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
            if m:
                result[key] = {"score": float(m.group(1)), "reason": f"{key} 自动提取"}
                break
        
        if key not in result:
            # 尝试提取 reason
            reason_patterns = [
                rf'["\']?{key}["\']?.*?["\']reason["\']?\s*[:：]\s*["\']([^"\']+)',
            ]
            for rp in reason_patterns:
                rm = re.search(rp, text, re.IGNORECASE | re.DOTALL)
                if rm and key in result:
                    result[key]["reason"] = rm.group(1).strip()

    # 如果至少提取到了一半以上的维度就算部分成功
    if len(result) >= len(DIMENSION_KEYS) // 2:
        logger.warning(f"使用降级提取，仅获取到 {len(result)}/{len(DIMENSION_KEYS)} 个维度")
        return result
    
    return None


def _has_required_fields(data: dict) -> bool:
    """检查数据是否包含足够的维度字段"""
    scores = data.get("scores", data)
    count = sum(1 for k in DIMENSION_KEYS if k in scores)
    return count >= len(DIMENSION_KEYS) // 2
