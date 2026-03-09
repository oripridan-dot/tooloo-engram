"""Tests for multi-model profiles, archetype mandates, stress mandates, and weakness scoring."""

from __future__ import annotations

import pytest

from experiments.project_engram.harness.config import (
    ALL_MODELS,
    ARCHETYPE_MANDATES,
    EXTENDED_MANDATES,
    FULL_MANDATES,
    L1_CLI_TOOL,
    L2_DATA_PIPELINE,
    L2_DATA_TRANSFORM,
    L2_MICROSERVICE,
    L3_ASYNC_PIPELINE,
    L3_FRONTEND_SPA,
    L4_HYPER_COMPLEX,
    MODEL_BY_TIER,
    MODEL_CLAUDE_OPUS_4,
    MODEL_CLAUDE_SONNET_4,
    MODEL_DEEPSEEK_V3,
    MODEL_GEMINI_31_PRO,
    MODEL_GEMINI_FLASH_LITE,
    MODEL_GPT5,
    MODEL_GPT5_MINI,
    MODEL_LLAMA_4_MAVERICK,
    PRIMARY_MODELS,
    STRESS_MANDATES,
    StressMandate,
    WeaknessProfile,
    get_weakness,
)
from experiments.project_engram.harness.mock_llm import MockLLM

# ── Model Profiles ───────────────────────────────────────────


class TestModelProfile:
    def test_all_models_defined(self):
        assert len(ALL_MODELS) == 8

    def test_primary_models(self):
        assert len(PRIMARY_MODELS) == 2
        assert MODEL_GEMINI_31_PRO in PRIMARY_MODELS
        assert MODEL_GEMINI_FLASH_LITE in PRIMARY_MODELS

    def test_model_tiers(self):
        assert MODEL_GEMINI_FLASH_LITE.tier == "fast"
        assert MODEL_GPT5_MINI.tier == "fast"
        assert MODEL_CLAUDE_SONNET_4.tier == "flash"
        assert MODEL_DEEPSEEK_V3.tier == "flash"
        assert MODEL_LLAMA_4_MAVERICK.tier == "flash"
        assert MODEL_GEMINI_31_PRO.tier == "pro"
        assert MODEL_CLAUDE_OPUS_4.tier == "pro"
        assert MODEL_GPT5.tier == "pro"

    def test_quality_factors_ordered(self):
        # Fast tier < flash tier < pro tier (generally)
        assert MODEL_GEMINI_FLASH_LITE.quality_factor < MODEL_CLAUDE_OPUS_4.quality_factor
        assert MODEL_GPT5_MINI.quality_factor < MODEL_GPT5.quality_factor
        assert MODEL_CLAUDE_OPUS_4.quality_factor == 1.0  # Apex model

    def test_label_format(self):
        assert MODEL_GEMINI_31_PRO.label == "Gemini-3.1-Pro (pro)"
        assert MODEL_CLAUDE_OPUS_4.label == "Claude-Opus-4 (pro)"
        assert MODEL_GEMINI_FLASH_LITE.label == "Gemini-Flash-Lite (fast)"

    def test_model_by_tier(self):
        assert "fast" in MODEL_BY_TIER
        assert "flash" in MODEL_BY_TIER
        assert "pro" in MODEL_BY_TIER
        assert len(MODEL_BY_TIER["fast"]) == 2
        assert len(MODEL_BY_TIER["flash"]) == 3
        assert len(MODEL_BY_TIER["pro"]) == 3


# ── Weakness Profiles ────────────────────────────────────────


class TestWeaknessProfile:
    def test_all_models_have_weakness(self):
        for model in ALL_MODELS:
            wp = get_weakness(model.name)
            assert isinstance(wp, WeaknessProfile)

    def test_opus_strongest(self):
        opus = get_weakness("Claude-Opus-4")
        for model in ALL_MODELS:
            other = get_weakness(model.name)
            assert opus.complex_logic_penalty >= other.complex_logic_penalty

    def test_flash_lite_weakest_concurrency(self):
        lite = get_weakness("Gemini-Flash-Lite")
        assert lite.concurrency_penalty < 0.75

    def test_hallucination_rates(self):
        assert get_weakness("Claude-Opus-4").hallucination_rate < 0.02
        assert get_weakness("Gemini-Flash-Lite").hallucination_rate > 0.10
        assert get_weakness("GPT-5-mini").hallucination_rate > 0.08

    def test_unknown_model_returns_default(self):
        wp = get_weakness("NonExistent-Model-99")
        assert wp.complex_logic_penalty == 1.0
        assert wp.hallucination_rate == 0.0


# ── Mock LLM with Model Profiles ────────────────────────────


class TestMockLLMWithProfile:
    def test_uses_profile_latency(self):
        mock = MockLLM(model_profile=MODEL_GEMINI_31_PRO)
        mock.query("sys", "test", "pro")
        assert mock.call_log[-1]["model_name"] == "Gemini-3.1-Pro"

    def test_cost_varies_by_model(self):
        cheap = MockLLM(model_profile=MODEL_GEMINI_FLASH_LITE)
        expensive = MockLLM(model_profile=MODEL_CLAUDE_OPUS_4)
        cheap.query("sys", "Generate calculator", "fast")
        expensive.query("sys", "Generate calculator", "pro")
        assert cheap.total_cost < expensive.total_cost

    def test_token_efficiency_affects_output(self):
        efficient = MockLLM(model_profile=MODEL_GEMINI_FLASH_LITE)
        verbose = MockLLM(model_profile=MODEL_CLAUDE_OPUS_4)

        efficient.query("sys", "test prompt", "fast")
        verbose.query("sys", "test prompt", "pro")

        assert (
            efficient.call_log[-1]["completion_tokens"] <= verbose.call_log[-1]["completion_tokens"]
        )

    def test_primary_models_cost_difference(self):
        pro = MockLLM(model_profile=MODEL_GEMINI_31_PRO)
        lite = MockLLM(model_profile=MODEL_GEMINI_FLASH_LITE)
        pro.query("sys", "Generate calculator", "pro")
        lite.query("sys", "Generate calculator", "fast")
        assert lite.total_cost < pro.total_cost


# ── Archetype Mandates ───────────────────────────────────────


class TestArchetypeMandates:
    def test_cli_mandate(self):
        assert L1_CLI_TOOL.level == "L1"
        assert len(L1_CLI_TOOL.expected_files) == 1
        assert "cli/search.py" in L1_CLI_TOOL.expected_files

    def test_microservice_mandate(self):
        assert L2_MICROSERVICE.level == "L2"
        assert len(L2_MICROSERVICE.expected_files) == 4
        assert "services/auth_service.py" in L2_MICROSERVICE.expected_files

    def test_pipeline_mandate(self):
        assert L2_DATA_PIPELINE.level == "L2"
        assert len(L2_DATA_PIPELINE.expected_files) == 5
        assert "pipeline/orchestrator.py" in L2_DATA_PIPELINE.expected_files

    def test_spa_mandate(self):
        assert L3_FRONTEND_SPA.level == "L3"
        assert len(L3_FRONTEND_SPA.expected_files) == 12
        assert "frontend/TaskBoard.tsx" in L3_FRONTEND_SPA.expected_files

    def test_extended_mandates_includes_all(self):
        assert len(EXTENDED_MANDATES) == 7  # 3 core + 4 archetypes

    def test_archetype_mandates_count(self):
        assert len(ARCHETYPE_MANDATES) == 4


# ── Stress Mandates ──────────────────────────────────────────


class TestStressMandates:
    def test_stress_mandates_count(self):
        assert len(STRESS_MANDATES) == 3

    def test_full_mandates_count(self):
        assert len(FULL_MANDATES) == 10  # 3 core + 4 archetype + 3 stress

    def test_data_transform_tags(self):
        assert isinstance(L2_DATA_TRANSFORM, StressMandate)
        assert "data_manipulation" in L2_DATA_TRANSFORM.stress_tags
        assert "complex_logic" in L2_DATA_TRANSFORM.stress_tags

    def test_async_pipeline_tags(self):
        assert isinstance(L3_ASYNC_PIPELINE, StressMandate)
        assert "concurrency" in L3_ASYNC_PIPELINE.stress_tags
        assert "long_context" in L3_ASYNC_PIPELINE.stress_tags

    def test_hyper_complex_tags(self):
        assert isinstance(L4_HYPER_COMPLEX, StressMandate)
        assert "complex_logic" in L4_HYPER_COMPLEX.stress_tags
        assert "concurrency" in L4_HYPER_COMPLEX.stress_tags

    def test_hyper_complex_file_count(self):
        assert len(L4_HYPER_COMPLEX.expected_files) == 12

    def test_stress_mandates_are_mandates(self):
        from experiments.project_engram.harness.config import Mandate

        for sm in STRESS_MANDATES:
            assert isinstance(sm, Mandate)


# ── Stress Templates ─────────────────────────────────────────


class TestStressTemplates:
    def test_data_transform_templates(self):
        mock = MockLLM()
        templates = mock.get_templates("L2", "Complex Data Transform")
        assert "transforms/normalizer.py" in templates
        assert "transforms/deduplicator.py" in templates
        assert "transforms/validator.py" in templates
        assert "tests/test_transforms.py" in templates

    def test_async_pipeline_templates(self):
        mock = MockLLM()
        templates = mock.get_templates("L3", "Async Event Pipeline")
        assert "events/event_bus.py" in templates
        assert "middleware/circuit_breaker.py" in templates
        assert len(templates) == 10

    def test_distributed_scheduler_templates(self):
        mock = MockLLM()
        templates = mock.get_templates("L3", "Distributed Task Scheduler")
        assert "scheduler/dag_executor.py" in templates
        assert "consistency/vector_clock.py" in templates
        assert "consistency/crdt_counter.py" in templates
        assert len(templates) == 12

    def test_stress_templates_parseable(self):
        """All Python stress templates should be AST-parseable."""
        import ast

        mock = MockLLM()
        for name in [
            "Complex Data Transform",
            "Async Event Pipeline",
            "Distributed Task Scheduler",
        ]:
            templates = mock.get_templates("L3", name)
            for path, source in templates.items():
                if path.endswith(".py"):
                    try:
                        ast.parse(source)
                    except SyntaxError as e:
                        pytest.fail(f"SyntaxError in {name}/{path}: {e}")

    def test_stress_keyword_detection(self):
        """MockLLM._select_template should detect stress keywords."""
        mock = MockLLM()
        resp_data = mock.query("sys", "Create normalizer for data transforms", "flash")
        assert "normaliz" in resp_data.lower() or "def " in resp_data

        resp_event = mock.query("sys", "Build an event bus with async handlers", "flash")
        assert "event" in resp_event.lower() or "class " in resp_event

        resp_dist = mock.query("sys", "Implement distributed DAG scheduler consensus", "flash")
        assert "dag" in resp_dist.lower() or "class " in resp_dist


# ── Archetype Templates ─────────────────────────────────────


class TestArchetypeTemplates:
    def test_cli_templates(self):
        mock = MockLLM()
        templates = mock.get_templates("L1", "File Search CLI")
        assert "cli/search.py" in templates
        assert "def search_by_name" in templates["cli/search.py"]

    def test_auth_templates(self):
        mock = MockLLM()
        templates = mock.get_templates("L2", "Auth Microservice")
        assert len(templates) == 4
        assert "services/auth_service.py" in templates

    def test_pipeline_templates(self):
        mock = MockLLM()
        templates = mock.get_templates("L2", "ETL Data Pipeline")
        assert len(templates) == 5
        assert "pipeline/orchestrator.py" in templates

    def test_spa_templates(self):
        mock = MockLLM()
        templates = mock.get_templates("L3", "Task Board SPA")
        assert len(templates) == 12
        assert "frontend/TaskBoard.tsx" in templates

    def test_fallback_to_level(self):
        mock = MockLLM()
        templates = mock.get_templates("L1", "Unknown Mandate")
        assert "utils/calculator.py" in templates

    def test_archetype_templates_parseable(self):
        """All Python archetype templates should be AST-parseable."""
        import ast

        mock = MockLLM()
        for name in ["File Search CLI", "Auth Microservice", "ETL Data Pipeline", "Task Board SPA"]:
            level = {
                "File Search CLI": "L1",
                "Auth Microservice": "L2",
                "ETL Data Pipeline": "L2",
                "Task Board SPA": "L3",
            }[name]
            templates = mock.get_templates(level, name)
            for path, source in templates.items():
                if path.endswith(".py"):
                    try:
                        ast.parse(source)
                    except SyntaxError as e:
                        pytest.fail(f"SyntaxError in {name}/{path}: {e}")
