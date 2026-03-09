"""Tests for harness.live_llm — Standalone Gemini + Embeddings client."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from experiments.project_engram.harness.live_llm import (
    EmbeddingEngine,
    LiveLLM,
    LLMCallRecord,
)

# ── LLMCallRecord ───────────────────────────────────────────


class TestLLMCallRecord:
    def test_default_success(self):
        r = LLMCallRecord(
            agent="test",
            model_tier="flash",
            model_name="gemini-2.0-flash",
            prompt_tokens=100,
            completion_tokens=50,
            latency_s=0.5,
            cost_usd=0.001,
        )
        assert r.success is True
        assert r.error == ""

    def test_error_record(self):
        r = LLMCallRecord(
            agent="test",
            model_tier="pro",
            model_name="gemini-2.0-flash",
            prompt_tokens=100,
            completion_tokens=0,
            latency_s=0.1,
            cost_usd=0.0,
            success=False,
            error="timeout",
        )
        assert r.success is False


# ── LiveLLM construction ────────────────────────────────────


class TestLiveLLMConstruction:
    def test_requires_api_key(self):
        with patch.dict(os.environ, {}, clear=True):
            env_backup = os.environ.pop("GEMINI_API_KEY", None)
            try:
                with pytest.raises(ValueError, match="GEMINI_API_KEY"):
                    LiveLLM(api_key="")
            finally:
                if env_backup:
                    os.environ["GEMINI_API_KEY"] = env_backup

    def test_accepts_explicit_key(self):
        llm = LiveLLM(api_key="test-key-123")
        assert llm.api_key == "test-key-123"

    def test_defaults(self):
        llm = LiveLLM(api_key="test-key")
        assert llm.total_cost == 0.0
        assert llm.budget_cap_usd == 2.0
        assert llm.call_log == []


# ── LiveLLM.query (mocked) ──────────────────────────────────


class TestLiveLLMQuery:
    def test_budget_cap_enforced(self):
        llm = LiveLLM(api_key="test", budget_cap_usd=0.001)
        llm.total_cost = 0.002
        with pytest.raises(RuntimeError, match="Budget cap"):
            llm.query("sys", "prompt", "flash")

    def test_query_records_call(self):
        llm = LiveLLM(api_key="test")

        mock_response = MagicMock()
        mock_response.text = "def hello(): pass"
        mock_response.usage_metadata = MagicMock()
        mock_response.usage_metadata.prompt_token_count = 50
        mock_response.usage_metadata.candidates_token_count = 10

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response
        llm._client = mock_client

        result = llm.query("system", "prompt", "flash")
        assert result == "def hello(): pass"
        assert len(llm.call_log) == 1
        assert llm.call_log[0].success is True
        assert llm.total_cost > 0

    def test_query_handles_error(self):
        llm = LiveLLM(api_key="test")
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = Exception("API error")
        llm._client = mock_client

        result = llm.query("system", "prompt", "flash")
        assert result == ""
        assert len(llm.call_log) == 1
        assert llm.call_log[0].success is False
        assert "API error" in llm.call_log[0].error

    def test_cost_accumulates(self):
        llm = LiveLLM(api_key="test")

        mock_response = MagicMock()
        mock_response.text = "code"
        mock_response.usage_metadata = MagicMock()
        mock_response.usage_metadata.prompt_token_count = 1000
        mock_response.usage_metadata.candidates_token_count = 500

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response
        llm._client = mock_client

        llm.query("s", "p", "pro")
        cost1 = llm.total_cost
        llm.query("s", "p", "pro")
        assert llm.total_cost > cost1

    def test_model_mapping(self):
        llm = LiveLLM(api_key="test")
        mock_response = MagicMock()
        mock_response.text = "ok"
        mock_response.usage_metadata = None

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response
        llm._client = mock_client

        llm.query("s", "p", "fast")
        assert llm.call_log[-1].model_name == "gemini-2.0-flash-lite"

        llm.query("s", "p", "flash")
        assert llm.call_log[-1].model_name == "gemini-2.0-flash"


# ── EmbeddingEngine ──────────────────────────────────────────


class TestEmbeddingEngine:
    @pytest.fixture(autouse=True)
    def _skip_if_no_torch(self):
        try:
            import sentence_transformers  # noqa: F401
        except ImportError:
            pytest.skip("sentence-transformers not installed")

    def test_encode_returns_array(self):
        engine = EmbeddingEngine()
        embeddings = engine.encode(["hello", "world"])
        assert len(embeddings) == 2
        assert embeddings.shape[1] == 384  # MiniLM embedding dim

    def test_semantic_search_returns_tuples(self):
        engine = EmbeddingEngine()
        results = engine.semantic_search(
            "add two numbers",
            ["calculator addition", "delete file", "send email"],
            top_k=2,
        )
        assert len(results) == 2
        idx, score = results[0]
        assert isinstance(idx, int)
        assert isinstance(score, float)
        # "calculator addition" should be most similar to "add two numbers"
        assert idx == 0

    def test_find_similar_engrams_empty(self):
        engine = EmbeddingEngine()
        results = engine.find_similar_engrams("test", [])
        assert results == []

    def test_find_similar_engrams_ranking(self):
        engine = EmbeddingEngine()
        intents = [
            "Authenticate user via JWT token",
            "Calculate sum of two integers",
            "Send notification email to user",
            "Add numbers for calculator module",
        ]
        results = engine.find_similar_engrams("math addition function", intents, top_k=2)
        # Indices 1 and 3 should rank highest (math-related)
        top_indices = {idx for idx, _ in results}
        assert 1 in top_indices or 3 in top_indices


# ── Integration: EmbeddingEngine + EngramGraph ───────────────


class TestEmbeddingGraphIntegration:
    @pytest.fixture(autouse=True)
    def _skip_if_no_torch(self):
        try:
            import sentence_transformers  # noqa: F401
        except ImportError:
            pytest.skip("sentence-transformers not installed")

    def test_semantic_search_on_graph_intents(self):
        from engram_v2.graph_store import EngramGraph
        from engram_v2.schema import LogicEngram

        graph = EngramGraph()
        intents = [
            "User authentication via JWT",
            "Calculate sum of numbers",
            "Send email notification",
            "CRUD operations for todo list",
        ]
        for intent in intents:
            graph.add_engram(
                LogicEngram(
                    intent=intent,
                    ast_signature=f"def {intent[:10].lower().replace(' ', '_')}():",
                    logic_body="pass",
                )
            )

        engine = EmbeddingEngine()
        all_intents = [e.intent for e in graph._engrams.values()]
        results = engine.find_similar_engrams("math addition", all_intents, top_k=2)
        assert len(results) == 2
        # "Calculate sum of numbers" should rank high
        top_intent_idx = results[0][0]
        assert (
            "sum" in all_intents[top_intent_idx].lower()
            or "calc" in all_intents[top_intent_idx].lower()
        )
