"""Conftest for tooloo-engram tests — sets up sys.path for module resolution."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Add workspace root (for experiments.project_engram.*)
_workspace = Path(__file__).parent.parent.parent
if str(_workspace) not in sys.path:
    sys.path.insert(0, str(_workspace))

# Add tooloo-engram root (for training_camp.*)
_tooloo_engram_root = Path(__file__).parent.parent
if str(_tooloo_engram_root) not in sys.path:
    sys.path.insert(0, str(_tooloo_engram_root))

# ── Source code fixtures (used by test_compiler_drone, test_ast_decomposer, etc.) ──

SIMPLE_MODULE = '''\
"""A simple calculator module."""

from __future__ import annotations


def add(a: int, b: int) -> int:
    return a + b


def subtract(a: int, b: int) -> int:
    return a - b
'''

CLASS_MODULE = '''\
"""In-memory todo service."""

from __future__ import annotations
from datetime import datetime
from .todo import TodoItem


class TodoService:
    """CRUD service."""

    _counter: int = 0

    def __init__(self) -> None:
        self._store: dict[int, TodoItem] = {}
        self._next_id = 1

    def create(self, title: str) -> TodoItem:
        item = TodoItem(id=self._next_id, title=title, created=datetime.now())
        self._store[self._next_id] = item
        self._next_id += 1
        return item

    def get(self, item_id: int) -> TodoItem | None:
        return self._store.get(item_id)
'''

EMPTY_MODULE = ""

MULTI_CLASS_MODULE = '''\
"""Two classes, one inherits."""

from dataclasses import dataclass


@dataclass
class Base:
    name: str


@dataclass
class Child(Base):
    age: int
'''

TSX_SOURCE = """\
import React from 'react';

interface Props {
  title: string;
}

export const Widget: React.FC<Props> = ({ title }) => {
  return <div className="widget">{title}</div>;
};
"""

ASYNC_MODULE = '''\
"""Async service."""

import asyncio


async def fetch_data(url: str) -> dict:
    await asyncio.sleep(0.1)
    return {"url": url, "data": "ok"}


async def process(url: str) -> str:
    result = await fetch_data(url)
    return result["data"]
'''

MODULE_WITH_CONSTANTS = '''\
"""Settings module."""

MAX_RETRIES = 3
DEFAULT_TIMEOUT = 30.0
ALLOWED_HOSTS = ["localhost", "127.0.0.1"]


def get_setting(key: str) -> str:
    return ""
'''

# ── Graph fixtures ────────────────────────────────────────────────────────────

from engram_v2.graph_store import EngramGraph
from engram_v2.schema import EdgeType, LogicEngram, SynapticEdge


@pytest.fixture
def empty_graph() -> EngramGraph:
    return EngramGraph()


@pytest.fixture
def two_node_graph() -> EngramGraph:
    """Graph with two connected engrams."""
    g = EngramGraph()
    e1 = LogicEngram(
        intent="models",
        ast_signature="class User:",
        logic_body="    pass",
        module_path="models/user.py",
    )
    e2 = LogicEngram(
        intent="service",
        ast_signature="class UserService:",
        logic_body="    pass",
        module_path="services/user_service.py",
    )
    g.add_engram(e1)
    g.add_engram(e2)
    g.add_edge(
        SynapticEdge(
            source_id=e2.engram_id,
            target_id=e1.engram_id,
            edge_type=EdgeType.IMPORTS,
        )
    )
    return g
