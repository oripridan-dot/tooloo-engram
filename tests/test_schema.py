"""Tests for engram.schema — LogicEngram, SynapticEdge, ContextTensor."""

from __future__ import annotations

from uuid import UUID, uuid4

from engram_v2.schema import (
    ContextTensor,
    Domain,
    EdgeType,
    Language,
    LogicEngram,
    SynapticEdge,
)

# ── LogicEngram ──────────────────────────────────────────────


class TestLogicEngram:
    def test_default_fields(self):
        e = LogicEngram(intent="test", ast_signature="def f():", logic_body="pass")
        assert e.language == Language.PYTHON
        assert e.domain == Domain.BACKEND
        assert isinstance(e.engram_id, UUID)
        assert e.parent_engram_id is None
        assert e.module_path == ""

    def test_token_count(self):
        e = LogicEngram(intent="t", ast_signature="s", logic_body="a" * 100)
        assert e.token_count == 25  # 100 // 4

    def test_checksum_deterministic(self):
        e = LogicEngram(intent="t", ast_signature="s", logic_body="hello")
        c1 = e.checksum
        c2 = e.checksum
        assert c1 == c2
        assert len(c1) == 64  # SHA-256 hex

    def test_different_body_different_checksum(self):
        e1 = LogicEngram(intent="t", ast_signature="s", logic_body="hello")
        e2 = LogicEngram(intent="t", ast_signature="s", logic_body="world")
        assert e1.checksum != e2.checksum

    def test_to_dict_roundtrip(self):
        e = LogicEngram(
            intent="create user",
            ast_signature="def create(name: str) -> User:",
            logic_body="return User(name=name)",
            language=Language.PYTHON,
            domain=Domain.BACKEND,
            module_path="services/user.py",
        )
        d = e.to_dict()
        restored = LogicEngram.from_dict(d)
        assert restored.engram_id == e.engram_id
        assert restored.intent == e.intent
        assert restored.logic_body == e.logic_body
        assert restored.language == e.language
        assert restored.domain == e.domain
        assert restored.module_path == e.module_path

    def test_to_dict_with_parent(self):
        pid = uuid4()
        e = LogicEngram(
            intent="method",
            ast_signature="def m():",
            logic_body="pass",
            parent_engram_id=pid,
        )
        d = e.to_dict()
        assert d["parent_engram_id"] == str(pid)
        restored = LogicEngram.from_dict(d)
        assert restored.parent_engram_id == pid

    def test_to_dict_without_parent(self):
        e = LogicEngram(intent="t", ast_signature="s", logic_body="b")
        d = e.to_dict()
        assert d["parent_engram_id"] is None


# ── SynapticEdge ─────────────────────────────────────────────


class TestSynapticEdge:
    def test_default_fields(self):
        edge = SynapticEdge(source_id=uuid4(), target_id=uuid4())
        assert edge.edge_type == EdgeType.IMPORTS
        assert edge.weight == 1.0
        assert edge.verified is False
        assert isinstance(edge.edge_id, UUID)

    def test_to_dict_roundtrip(self):
        edge = SynapticEdge(
            source_id=uuid4(),
            target_id=uuid4(),
            edge_type=EdgeType.CALLS,
            weight=0.8,
            verified=True,
        )
        d = edge.to_dict()
        restored = SynapticEdge.from_dict(d)
        assert restored.edge_id == edge.edge_id
        assert restored.source_id == edge.source_id
        assert restored.target_id == edge.target_id
        assert restored.edge_type == EdgeType.CALLS
        assert restored.weight == 0.8
        assert restored.verified is True


# ── Enums ────────────────────────────────────────────────────


class TestEnums:
    def test_edge_types(self):
        assert EdgeType.IMPORTS.value == "imports"
        assert EdgeType.CALLS.value == "calls"
        assert EdgeType.INHERITS.value == "inherits"
        assert EdgeType.TESTS.value == "tests"
        assert EdgeType.RESOLVES.value == "resolves"
        assert EdgeType.CAUSES.value == "causes"

    def test_domains(self):
        assert Domain.BACKEND.value == "backend"
        assert Domain.FRONTEND.value == "frontend"
        assert Domain.TEST.value == "test"
        assert Domain.CONFIG.value == "config"

    def test_languages(self):
        assert Language.PYTHON.value == "python"
        assert Language.TYPESCRIPT.value == "typescript"
        assert Language.TSX.value == "tsx"


# ── ContextTensor ────────────────────────────────────────────


class TestContextTensor:
    def test_defaults(self):
        t = ContextTensor(
            target_engrams=[uuid4()],
            dependency_subgraph_json="{}",
            intent_chain=["test"],
        )
        assert t.token_budget == 8000
        assert t.assembled_prompt == ""
        assert isinstance(t.tensor_id, UUID)

    def test_token_count(self):
        t = ContextTensor(
            target_engrams=[],
            dependency_subgraph_json="{}",
            intent_chain=[],
            assembled_prompt="a" * 400,
        )
        assert t.token_count == 100

    def test_to_dict(self):
        eid = uuid4()
        t = ContextTensor(
            target_engrams=[eid],
            dependency_subgraph_json='{"test": true}',
            intent_chain=["do stuff"],
            token_budget=4000,
        )
        d = t.to_dict()
        assert str(eid) in d["target_engrams"]
        assert d["token_budget"] == 4000


class TestResolvesAndCauses:
    def test_resolves_edge_roundtrip(self):
        edge = SynapticEdge(
            source_id=uuid4(),
            target_id=uuid4(),
            edge_type=EdgeType.RESOLVES,
            weight=0.9,
        )
        d = edge.to_dict()
        restored = SynapticEdge.from_dict(d)
        assert restored.edge_type == EdgeType.RESOLVES
        assert restored.weight == 0.9

    def test_causes_edge_roundtrip(self):
        edge = SynapticEdge(
            source_id=uuid4(),
            target_id=uuid4(),
            edge_type=EdgeType.CAUSES,
            weight=0.5,
        )
        d = edge.to_dict()
        restored = SynapticEdge.from_dict(d)
        assert restored.edge_type == EdgeType.CAUSES
        assert restored.weight == 0.5
