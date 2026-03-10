"""
GraphPersistence — SQLite + WAL journaling for the EngramGraph.

Architecture:
  GraphPersistence   — atomic SQLite backend with WAL mode for concurrent reads
  DeltaCheckpoint    — immutable snapshot of graph state at a point in time
  DeltaSyncBus*      — is the in-memory WebSocket event bus (see delta_sync.py)
                       GraphPersistence only persists the *durable* deltas here.

Tables:
  engrams            — serialised LogicEngram rows (keyed by engram_id UUID)
  edges              — serialised SynapticEdge rows (keyed by edge_id UUID)
  checkpoints        — named graph snapshots for rollback and audit

Usage:
    store = GraphPersistence("core.engram.db")
    store.upsert_engram(engram)
    store.upsert_edge(edge)
    cid = store.checkpoint("v2-genesis")
    store.load_into_graph(graph)
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Generator
from uuid import UUID, uuid4

from .graph_store import EngramGraph
from .schema import LogicEngram, SynapticEdge

log = logging.getLogger(__name__)

# SQL DDL ─────────────────────────────────────────────────────

_DDL = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous  = NORMAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS engrams (
    engram_id   TEXT PRIMARY KEY,
    module_path TEXT NOT NULL DEFAULT '',
    domain      TEXT NOT NULL DEFAULT 'backend',
    intent      TEXT NOT NULL DEFAULT '',
    payload     TEXT NOT NULL,            -- full JSON from LogicEngram.to_dict()
    checksum    TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS edges (
    edge_id     TEXT PRIMARY KEY,
    source_id   TEXT NOT NULL,
    target_id   TEXT NOT NULL,
    edge_type   TEXT NOT NULL,
    payload     TEXT NOT NULL,            -- full JSON from SynapticEdge.to_dict()
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    FOREIGN KEY (source_id) REFERENCES engrams(engram_id) ON DELETE CASCADE,
    FOREIGN KEY (target_id) REFERENCES engrams(engram_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS checkpoints (
    checkpoint_id   TEXT PRIMARY KEY,
    label           TEXT NOT NULL,
    engram_count    INTEGER NOT NULL DEFAULT 0,
    edge_count      INTEGER NOT NULL DEFAULT 0,
    graph_snapshot  TEXT NOT NULL,        -- full serialised EngramGraph JSON
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_engrams_domain      ON engrams(domain);
CREATE INDEX IF NOT EXISTS idx_engrams_module_path ON engrams(module_path);
CREATE INDEX IF NOT EXISTS idx_edges_source        ON edges(source_id);
CREATE INDEX IF NOT EXISTS idx_edges_target        ON edges(target_id);
"""


# ─────────────────────────────────────────────────────────────


@dataclass
class DeltaCheckpoint:
    """Immutable snapshot of graph state captured at checkpoint time."""

    checkpoint_id: UUID
    label: str
    engram_count: int
    edge_count: int
    graph_snapshot: str  # serialised EngramGraph JSON
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict:
        return {
            "checkpoint_id": str(self.checkpoint_id),
            "label": self.label,
            "engram_count": self.engram_count,
            "edge_count": self.edge_count,
            "created_at": self.created_at.isoformat(),
        }


class GraphPersistence:
    """
    SQLite-backed durable store for an EngramGraph.

    Thread-safe via a per-instance write-lock. Readers can run concurrently
    thanks to WAL mode. All mutating operations are atomic transactions.
    """

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._path = Path(db_path) if db_path != ":memory:" else db_path  # type: ignore[assignment]
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    # ── Lifecycle ─────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        path = str(self._path)
        conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        conn = self._connect()
        conn.executescript(_DDL)
        conn.commit()
        self._conn = conn
        log.debug("GraphPersistence initialised at %s", self._path)

    @contextmanager
    def _transaction(self) -> Generator[sqlite3.Connection, None, None]:
        """Yield a connection inside an exclusive write transaction."""
        assert self._conn is not None
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    # ── Engram CRUD ───────────────────────────────────────────

    def upsert_engram(self, engram: LogicEngram) -> None:
        """Insert or replace a LogicEngram row (by engram_id PK)."""
        now = datetime.now(UTC).isoformat()
        payload = json.dumps(engram.to_dict())
        with self._transaction() as conn:
            conn.execute(
                """
                INSERT INTO engrams (engram_id, module_path, domain, intent, payload,
                                     checksum, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(engram_id) DO UPDATE SET
                    module_path = excluded.module_path,
                    domain      = excluded.domain,
                    intent      = excluded.intent,
                    payload     = excluded.payload,
                    checksum    = excluded.checksum,
                    updated_at  = excluded.updated_at
                """,
                (
                    str(engram.engram_id),
                    engram.module_path,
                    engram.domain.value,
                    engram.intent,
                    payload,
                    engram.checksum,
                    now,
                    now,
                ),
            )
        log.debug("upsert_engram %s", engram.engram_id)

    def delete_engram(self, engram_id: UUID) -> bool:
        """Remove an engram and its dependent edges (CASCADE). Returns True if found."""
        with self._transaction() as conn:
            cur = conn.execute(
                "DELETE FROM engrams WHERE engram_id = ?", (str(engram_id),)
            )
            deleted = cur.rowcount > 0
        log.debug("delete_engram %s deleted=%s", engram_id, deleted)
        return deleted

    def get_engram(self, engram_id: UUID) -> LogicEngram | None:
        assert self._conn is not None
        row = self._conn.execute(
            "SELECT payload FROM engrams WHERE engram_id = ?", (str(engram_id),)
        ).fetchone()
        if row is None:
            return None
        return LogicEngram.from_dict(json.loads(row["payload"]))

    def all_engrams(self) -> list[LogicEngram]:
        assert self._conn is not None
        rows = self._conn.execute("SELECT payload FROM engrams ORDER BY created_at").fetchall()
        return [LogicEngram.from_dict(json.loads(r["payload"])) for r in rows]

    def engram_count(self) -> int:
        assert self._conn is not None
        row = self._conn.execute("SELECT COUNT(*) AS n FROM engrams").fetchone()
        return row["n"]

    # ── Edge CRUD ─────────────────────────────────────────────

    def upsert_edge(self, edge: SynapticEdge) -> None:
        """Insert or replace a SynapticEdge row."""
        now = datetime.now(UTC).isoformat()
        payload = json.dumps(edge.to_dict())
        with self._transaction() as conn:
            conn.execute(
                """
                INSERT INTO edges (edge_id, source_id, target_id, edge_type, payload,
                                   created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(edge_id) DO UPDATE SET
                    source_id  = excluded.source_id,
                    target_id  = excluded.target_id,
                    edge_type  = excluded.edge_type,
                    payload    = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (
                    str(edge.edge_id),
                    str(edge.source_id),
                    str(edge.target_id),
                    edge.edge_type.value,
                    payload,
                    now,
                    now,
                ),
            )
        log.debug("upsert_edge %s", edge.edge_id)

    def delete_edge(self, edge_id: UUID) -> bool:
        with self._transaction() as conn:
            cur = conn.execute("DELETE FROM edges WHERE edge_id = ?", (str(edge_id),))
            return cur.rowcount > 0

    def get_edge(self, edge_id: UUID) -> SynapticEdge | None:
        assert self._conn is not None
        row = self._conn.execute(
            "SELECT payload FROM edges WHERE edge_id = ?", (str(edge_id),)
        ).fetchone()
        if row is None:
            return None
        return SynapticEdge.from_dict(json.loads(row["payload"]))

    def all_edges(self) -> list[SynapticEdge]:
        assert self._conn is not None
        rows = self._conn.execute("SELECT payload FROM edges ORDER BY created_at").fetchall()
        return [SynapticEdge.from_dict(json.loads(r["payload"])) for r in rows]

    def edge_count(self) -> int:
        assert self._conn is not None
        row = self._conn.execute("SELECT COUNT(*) AS n FROM edges").fetchone()
        return row["n"]

    # ── Checkpoints ───────────────────────────────────────────

    def checkpoint(self, label: str, graph: EngramGraph | None = None) -> UUID:
        """
        Persist a named checkpoint snapshot.

        If *graph* is supplied, the full serialised graph JSON is stored.
        Otherwise, the checkpoint records counts from the live tables.

        Returns the new checkpoint_id.
        """
        cid = uuid4()
        now = datetime.now(UTC).isoformat()
        n_engrams = self.engram_count()
        n_edges = self.edge_count()
        snapshot = graph.serialize() if graph is not None else "{}"
        with self._transaction() as conn:
            conn.execute(
                """
                INSERT INTO checkpoints
                    (checkpoint_id, label, engram_count, edge_count,
                     graph_snapshot, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (str(cid), label, n_engrams, n_edges, snapshot, now),
            )
        log.info("checkpoint '%s' id=%s engrams=%d edges=%d", label, cid, n_engrams, n_edges)
        return cid

    def list_checkpoints(self) -> list[DeltaCheckpoint]:
        assert self._conn is not None
        rows = self._conn.execute(
            "SELECT * FROM checkpoints ORDER BY created_at DESC"
        ).fetchall()
        result = []
        for r in rows:
            result.append(
                DeltaCheckpoint(
                    checkpoint_id=UUID(r["checkpoint_id"]),
                    label=r["label"],
                    engram_count=r["engram_count"],
                    edge_count=r["edge_count"],
                    graph_snapshot=r["graph_snapshot"],
                    created_at=datetime.fromisoformat(r["created_at"]),
                )
            )
        return result

    def restore_checkpoint(self, checkpoint_id: UUID) -> EngramGraph | None:
        """Reconstruct an EngramGraph from a stored checkpoint snapshot."""
        assert self._conn is not None
        row = self._conn.execute(
            "SELECT graph_snapshot FROM checkpoints WHERE checkpoint_id = ?",
            (str(checkpoint_id),),
        ).fetchone()
        if row is None:
            return None
        snapshot = row["graph_snapshot"]
        if snapshot == "{}":
            return None
        return EngramGraph.deserialize(snapshot)

    # ── Bulk Load ─────────────────────────────────────────────

    def load_into_graph(self, graph: EngramGraph) -> tuple[int, int]:
        """
        Hydrate *graph* in-place from the persisted engrams and edges.

        Returns (engrams_loaded, edges_loaded).
        Edges referencing unknown engrams are silently skipped (stale foreign key).
        """
        engrams = self.all_engrams()
        for eng in engrams:
            if not graph.has_engram(eng.engram_id):
                graph.add_engram(eng)

        edges = self.all_edges()
        loaded_edges = 0
        for edge in edges:
            try:
                if not graph.has_engram(edge.source_id) or not graph.has_engram(edge.target_id):
                    continue
                graph.add_edge(edge)
                loaded_edges += 1
            except Exception as exc:
                log.warning("Skipping edge %s during load: %s", edge.edge_id, exc)

        log.info("load_into_graph: %d engrams, %d edges", len(engrams), loaded_edges)
        return len(engrams), loaded_edges

    def persist_from_graph(self, graph: EngramGraph) -> tuple[int, int]:
        """
        Flush all engrams and edges from an in-memory *graph* to SQLite.

        Returns (engrams_written, edges_written).
        """
        engrams_written = 0
        for engram in graph._engrams.values():  # noqa: SLF001
            self.upsert_engram(engram)
            engrams_written += 1

        edges_written = 0
        for edge in graph._edges.values():  # noqa: SLF001
            self.upsert_edge(edge)
            edges_written += 1

        log.info("persist_from_graph: %d engrams, %d edges", engrams_written, edges_written)
        return engrams_written, edges_written
