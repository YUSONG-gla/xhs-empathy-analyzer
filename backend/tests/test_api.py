"""
集成测试 - API 路由层 (api/routes.py)

使用 FastAPI TestClient + unittest.mock 模拟 LLM 调用，
测试完整的 HTTP 请求 → 响应链路，无需真实 API Key。

覆盖场景:
  - POST /api/score 正常评分流程
  - 请求体校验错误（文本过短/为空）
  - LLM 调用失败时返回 502
  - JSON 解析失败时返回 502
  - GET /health 健康检查
"""

import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient

from main import app
from utils.parser import DIMENSION_KEYS


# ==================== 测试数据 ====================

SAMPLE_TEXT = (
    "今天走在雨中，街灯的倒影在积水中晃动，我突然想起了她说过的话。"
    "心里有什么东西慢慢沉下去，像一块石头沉入水底，再也看不见了。"
)

MOCK_LLM_JSON = json.dumps({
    "scores": {
        k: {"score": 6, "reason": f"{k} 测试理由"}
        for k in DIMENSION_KEYS
    },
    "evaluation": "该文本在情感表达方面较为细腻，具有一定的共情力。",
})


# ==================== Fixtures ====================

@pytest.fixture
def client():
    """FastAPI 测试客户端"""
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def mock_llm_call():
    """
    Mock LLM 调用，使所有测试不依赖真实 API Key。
    patch 路径指向 scorer.py 中实际使用的 create_llm_service。
    """
    mock_service = MagicMock()
    mock_service.model_name = "gpt-4o-mock"
    mock_service.call = AsyncMock(return_value=MOCK_LLM_JSON)

    with patch("services.scorer.create_llm_service", return_value=mock_service):
        yield mock_service


# ==================== 健康检查 ====================

class TestHealthCheck:
    def test_health_endpoint_returns_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["service"] == "heart-scorer"


# ==================== POST /api/score 正常路径 ====================

class TestScoreEndpoint:
    def test_successful_score(self, client, mock_llm_call):
        resp = client.post("/api/score", json={"text": SAMPLE_TEXT})
        assert resp.status_code == 200
        data = resp.json()

        assert data["success"] is True
        assert isinstance(data["total_score"], float)
        assert 0 < data["total_score"] <= 10
        assert len(data["dimensions"]) == 9
        assert "calculation_process" in data
        assert "evaluation" in data
        assert data["model_used"] == "gpt-4o-mock"

    def test_dimensions_have_required_fields(self, client, mock_llm_call):
        resp = client.post("/api/score", json={"text": SAMPLE_TEXT})
        assert resp.status_code == 200
        for dim in resp.json()["dimensions"]:
            assert "name" in dim
            assert "key" in dim
            assert "score" in dim
            assert "reason" in dim
            assert 0 <= dim["score"] <= 10

    def test_all_nine_dimensions_present(self, client, mock_llm_call):
        resp = client.post("/api/score", json={"text": SAMPLE_TEXT})
        returned_keys = {d["key"] for d in resp.json()["dimensions"]}
        assert returned_keys == set(DIMENSION_KEYS)

    def test_response_has_x_request_id_header(self, client, mock_llm_call):
        resp = client.post("/api/score", json={"text": SAMPLE_TEXT})
        assert "x-request-id" in resp.headers


# ==================== 请求校验错误 ====================

class TestRequestValidation:
    def test_text_too_short_returns_422(self, client):
        resp = client.post("/api/score", json={"text": "短"})
        assert resp.status_code == 422

    def test_empty_text_returns_422(self, client):
        resp = client.post("/api/score", json={"text": ""})
        assert resp.status_code == 422

    def test_missing_text_field_returns_422(self, client):
        resp = client.post("/api/score", json={})
        assert resp.status_code == 422

    def test_text_too_long_returns_422(self, client):
        long_text = "a" * 10001
        resp = client.post("/api/score", json={"text": long_text})
        assert resp.status_code == 422

    def test_non_json_body_returns_422(self, client):
        resp = client.post(
            "/api/score",
            content="not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 422


# ==================== LLM 服务失败场景 ====================

class TestLLMFailures:
    def test_llm_call_failure_returns_502(self, client):
        mock_service = MagicMock()
        mock_service.model_name = "gpt-4o-mock"
        mock_service.call = AsyncMock(side_effect=RuntimeError("网络超时"))

        with patch("services.scorer.create_llm_service", return_value=mock_service):
            resp = client.post("/api/score", json={"text": SAMPLE_TEXT})

        assert resp.status_code == 502
        assert "LLM" in resp.json()["detail"]

    def test_llm_returns_garbage_json_returns_502(self, client):
        mock_service = MagicMock()
        mock_service.model_name = "gpt-4o-mock"
        mock_service.call = AsyncMock(return_value="完全无效的文本，无任何JSON内容@#$%")

        with patch("services.scorer.create_llm_service", return_value=mock_service):
            resp = client.post("/api/score", json={"text": SAMPLE_TEXT})

        assert resp.status_code == 502

    def test_llm_returns_partial_json_still_succeeds(self, client):
        """部分 JSON（超过半数维度可提取）应仍然成功"""
        partial = json.dumps({
            "scores": {
                k: {"score": 6, "reason": "ok"}
                for k in DIMENSION_KEYS[:5]   # 只有前5个维度
            },
            "evaluation": "部分评分",
        })
        mock_service = MagicMock()
        mock_service.model_name = "gpt-4o-mock"
        mock_service.call = AsyncMock(return_value=partial)

        with patch("services.scorer.create_llm_service", return_value=mock_service):
            resp = client.post("/api/score", json={"text": SAMPLE_TEXT})

        # 部分维度返回，缺失的自动补全，应该成功
        assert resp.status_code == 200
        assert resp.json()["success"] is True


# ==================== 频率限制测试 ====================

class TestRateLimit:
    def test_rate_limit_triggers_after_threshold(self, client, mock_llm_call):
        """
        快速连续发送超过限额的请求，最终应收到 429。
        注意：TestClient 默认使用同一 IP（127.0.0.1），
        此测试直接 patch 限速器使其拒绝。
        """
        from api import dependencies as dep_module

        original = dep_module._rate_limiter.is_allowed
        try:
            # 强制让限速器拒绝
            dep_module._rate_limiter.is_allowed = lambda ip: False
            resp = client.post("/api/score", json={"text": SAMPLE_TEXT})
            assert resp.status_code == 429
        finally:
            dep_module._rate_limiter.is_allowed = original
