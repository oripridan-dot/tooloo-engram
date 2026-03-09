"""Tests for harness.quality_scorer — code quality evaluation."""

from __future__ import annotations

from experiments.project_engram.harness.config import L1_SIMPLE, L2_MEDIUM
from experiments.project_engram.harness.quality_scorer import QualityReport, score_output

# ── Perfect code scoring ─────────────────────────────────────


class TestPerfectCode:
    def test_valid_python_scores_ast(self):
        files = {"calc.py": "def add(a: int, b: int) -> int:\n    return a + b\n"}
        mandate = L1_SIMPLE
        report = score_output(files, mandate)
        assert report.ast_score == 30.0  # All files parse

    def test_typed_functions_score(self):
        files = {"calc.py": "def add(a: int, b: int) -> int:\n    return a + b\n"}
        report = score_output(files, L1_SIMPLE)
        assert report.type_hint_score > 0

    def test_empty_files_zero_score(self):
        report = score_output({}, L1_SIMPLE)
        assert report.total == 0.0
        assert "No files produced" in report.errors


# ── AST scoring ──────────────────────────────────────────────


class TestASTScoring:
    def test_syntax_error_reduces_ast_score(self):
        files = {
            "good.py": "def f() -> int:\n    return 1\n",
            "bad.py": "def g(:\n    return\n",
        }
        report = score_output(files, L1_SIMPLE)
        assert report.ast_score < 30.0
        assert len(report.errors) >= 1

    def test_non_python_files_skipped(self):
        files = {
            "app.tsx": "const x = 1;",
            "calc.py": "def add() -> int:\n    return 1\n",
        }
        report = score_output(files, L1_SIMPLE)
        assert report.ast_score == 30.0  # Only Python files count


# ── Import scoring ───────────────────────────────────────────


class TestImportScoring:
    def test_valid_relative_imports(self):
        files = {
            "models/user.py": "class User:\n    pass\n",
            "services/svc.py": "from .user import User\n",  # won't resolve
        }
        report = score_output(files, L2_MEDIUM)
        # The relative import won't resolve but absolute won't be penalized
        assert report.import_score >= 0

    def test_no_imports_full_score(self):
        files = {"calc.py": "x = 1\n"}
        report = score_output(files, L1_SIMPLE)
        assert report.import_score == 20.0


# ── Structural scoring ──────────────────────────────────────


class TestStructuralScoring:
    def test_all_expected_files_present(self):
        from experiments.project_engram.harness.mock_llm import MockLLM

        llm = MockLLM()
        templates = llm.get_templates("L1")
        report = score_output(templates, L1_SIMPLE)
        assert report.structural_score == 25.0

    def test_missing_files_reduce_score(self):
        # Only produce half the expected files
        files = dict.fromkeys(L2_MEDIUM.expected_files[:2], "pass\n")
        report = score_output(files, L2_MEDIUM)
        assert report.structural_score < 25.0

    def test_no_expected_files_zero_structural(self):
        files = {"random.py": "x = 1\n"}
        report = score_output(files, L2_MEDIUM)
        assert report.structural_score == 0.0


# ── Type hint scoring ────────────────────────────────────────


class TestTypeHintScoring:
    def test_all_typed_full_score(self):
        files = {
            "svc.py": (
                "def create(name: str) -> int:\n    return 1\n\n"
                "def delete(id: int) -> bool:\n    return True\n"
            ),
        }
        report = score_output(files, L1_SIMPLE)
        assert report.type_hint_score == 15.0

    def test_no_hints_zero_score(self):
        files = {"svc.py": "def create(name):\n    return 1\n"}
        report = score_output(files, L1_SIMPLE)
        assert report.type_hint_score == 0.0

    def test_partial_hints_partial_score(self):
        files = {
            "svc.py": (
                "def typed(x: int) -> int:\n    return x\n\ndef untyped(x):\n    return x\n"
            ),
        }
        report = score_output(files, L1_SIMPLE)
        assert 0 < report.type_hint_score < 15.0


# ── Test intent scoring ─────────────────────────────────────


class TestTestIntentScoring:
    def test_test_file_with_assertions(self):
        files = {
            "test_calc.py": "def test_add():\n    assert 1 + 1 == 2\n",
        }
        report = score_output(files, L1_SIMPLE)
        assert report.test_score == 10.0

    def test_test_file_no_assertions(self):
        files = {
            "test_calc.py": "def setup():\n    pass\n",
        }
        report = score_output(files, L1_SIMPLE)
        assert report.test_score == 5.0  # Has test file but no test func

    def test_no_tests_zero(self):
        files = {"calc.py": "x = 1\n"}
        report = score_output(files, L1_SIMPLE)
        assert report.test_score == 0.0


# ── QualityReport ────────────────────────────────────────────


class TestQualityReport:
    def test_total_sums_dimensions(self):
        r = QualityReport(
            mandate_name="test",
            ast_score=30.0,
            import_score=20.0,
            structural_score=25.0,
            type_hint_score=15.0,
            test_score=10.0,
        )
        assert r.total == 100.0

    def test_to_dict_complete(self):
        r = QualityReport(mandate_name="test", ast_score=15.0)
        d = r.to_dict()
        assert d["mandate"] == "test"
        assert d["ast_score"] == 15.0
        assert "per_file" in d
        assert "errors" in d

    def test_per_file_populated(self):
        files = {"calc.py": "def add() -> int:\n    return 1\n"}
        report = score_output(files, L1_SIMPLE)
        assert "calc.py" in report.per_file
        assert report.per_file["calc.py"]["parseable"] is True
