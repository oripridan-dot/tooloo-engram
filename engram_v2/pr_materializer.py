"""
PRMaterializer — Git-Mind Protocol enforcement layer.

Constitutional mandate (from architecture rules):
  "TooLoo does not edit local files directly. All intent crystallisation
   must be formatted as graph mutations via EngramFusionAdapter,
   materialised as GitHub Pull Requests using PyGithub."

  Direct file writes are a CONSTITUTIONAL VIOLATION.

Architecture:
    GraphDiff         — compares two EngramGraph snapshots (before vs. after)
    CompiledArtifact  — a single file path + content pair produced by CompilerDrone
    PRMaterializer    — reads a GraphDiff, compiles changed engrams, creates a PR

Flow:
    baseline_graph  ──┐
                       ├─→ GraphDiff ──→ PRMaterializer.materialise()
    mutated_graph   ──┘                      ↓
                                       CompilerDrone (project engrams → files)
                                             ↓
                                       PyGithub  (branch + commit + PR)

Constitutional controls:
  - Dry-run mode: produces the diff + compiled content without touching GitHub
  - Branch names are deterministic (mandate-id prefixed)
  - Commit messages embed the mandate_id for full audit trail
  - No file is written to the local filesystem (all content goes directly to GitHub API)
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from .compiler_drone import OutputMode, compile_graph
from .schema import LogicEngram, SynapticEdge

if TYPE_CHECKING:
    from .graph_store import EngramGraph

log = logging.getLogger(__name__)


# ── Diff types ────────────────────────────────────────────────


class ChangeKind(StrEnum):
    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"


@dataclass
class EngramChange:
    """A single node-level change between two graph snapshots."""

    kind: ChangeKind
    engram_id: UUID
    before: LogicEngram | None = None   # None for ADDED
    after: LogicEngram | None = None    # None for DELETED


@dataclass
class EdgeChange:
    """A single edge-level change between two graph snapshots."""

    kind: ChangeKind
    edge_id: UUID
    before: SynapticEdge | None = None
    after: SynapticEdge | None = None


@dataclass
class GraphDiff:
    """
    Immutable diff between a baseline and a mutated EngramGraph.

    Produced by diff_graphs(). Consumed by PRMaterializer.
    """

    diff_id: UUID = field(default_factory=uuid4)
    engram_changes: list[EngramChange] = field(default_factory=list)
    edge_changes: list[EdgeChange] = field(default_factory=list)
    computed_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    @property
    def is_empty(self) -> bool:
        return not self.engram_changes and not self.edge_changes

    @property
    def added_engrams(self) -> list[EngramChange]:
        return [c for c in self.engram_changes if c.kind == ChangeKind.ADDED]

    @property
    def modified_engrams(self) -> list[EngramChange]:
        return [c for c in self.engram_changes if c.kind == ChangeKind.MODIFIED]

    @property
    def deleted_engrams(self) -> list[EngramChange]:
        return [c for c in self.engram_changes if c.kind == ChangeKind.DELETED]

    def summary(self) -> str:
        return (
            f"GraphDiff(+{len(self.added_engrams)} "
            f"~{len(self.modified_engrams)} "
            f"-{len(self.deleted_engrams)} engrams, "
            f"{len(self.edge_changes)} edge changes)"
        )

    def to_dict(self) -> dict:
        return {
            "diff_id": str(self.diff_id),
            "computed_at": self.computed_at,
            "engram_changes": len(self.engram_changes),
            "edge_changes": len(self.edge_changes),
            "added": len(self.added_engrams),
            "modified": len(self.modified_engrams),
            "deleted": len(self.deleted_engrams),
        }


# ── Compiled artifact ─────────────────────────────────────────


@dataclass
class CompiledArtifact:
    """A single file produced by compiling one or more LogicEngrams."""

    module_path: str           # e.g. "models/user.py"
    content: str               # full file contents
    engram_ids: list[UUID] = field(default_factory=list)
    content_sha256: str = ""

    def __post_init__(self) -> None:
        if not self.content_sha256:
            self.content_sha256 = hashlib.sha256(self.content.encode()).hexdigest()


# ── PR result ─────────────────────────────────────────────────


@dataclass
class PRResult:
    """Outcome of a materialise() call."""

    mandate_id: UUID
    diff: GraphDiff
    branch_name: str
    pr_url: str = ""          # empty in dry-run mode
    pr_number: int = 0
    artifacts: list[CompiledArtifact] = field(default_factory=list)
    dry_run: bool = False
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    error: str = ""

    @property
    def success(self) -> bool:
        return not self.error

    def to_dict(self) -> dict:
        return {
            "mandate_id": str(self.mandate_id),
            "diff_summary": self.diff.summary(),
            "branch_name": self.branch_name,
            "pr_url": self.pr_url,
            "pr_number": self.pr_number,
            "artifacts_count": len(self.artifacts),
            "dry_run": self.dry_run,
            "created_at": self.created_at,
            "success": self.success,
            "error": self.error,
        }


# ── Graph diff function ───────────────────────────────────────


def diff_graphs(before: EngramGraph, after: EngramGraph) -> GraphDiff:
    """
    Compute the diff between *before* and *after* EngramGraph snapshots.

    Engram identity is the engram_id. Modification is detected by checksum change.
    """
    diff = GraphDiff()

    before_ids: set[UUID] = set(before._engrams.keys())   # noqa: SLF001
    after_ids: set[UUID] = set(after._engrams.keys())     # noqa: SLF001

    added_ids = after_ids - before_ids
    for eid in added_ids:
        diff.engram_changes.append(
            EngramChange(
                kind=ChangeKind.ADDED,
                engram_id=eid,
                after=after._engrams[eid],  # noqa: SLF001
            )
        )

    for eid in (before_ids - after_ids):
        diff.engram_changes.append(
            EngramChange(
                kind=ChangeKind.DELETED,
                engram_id=eid,
                before=before._engrams[eid],  # noqa: SLF001
            )
        )

    for eid in (before_ids & after_ids):
        b = before._engrams[eid]   # noqa: SLF001
        a = after._engrams[eid]    # noqa: SLF001
        if b.checksum != a.checksum:
            diff.engram_changes.append(
                EngramChange(kind=ChangeKind.MODIFIED, engram_id=eid, before=b, after=a)
            )

    before_eids: set[UUID] = set(before._edges.keys())   # noqa: SLF001
    after_eids: set[UUID] = set(after._edges.keys())     # noqa: SLF001

    for eid in after_eids - before_eids:
        diff.edge_changes.append(
            EdgeChange(kind=ChangeKind.ADDED, edge_id=eid, after=after._edges[eid])  # noqa: SLF001
        )
    for eid in before_eids - after_eids:
        diff.edge_changes.append(
            EdgeChange(kind=ChangeKind.DELETED, edge_id=eid, before=before._edges[eid])  # noqa: SLF001
        )

    _ = added_ids  # suppress unused warning
    log.debug("diff_graphs: %s", diff.summary())
    return diff


# ── Mock GitHub client (swappable for live PyGithub) ─────────


class GitHubBackend:
    """
    Abstract interface for GitHub operations.

    Production implementation uses PyGithub:
        from github import Github
        gh = Github(os.environ["GITHUB_TOKEN"])
        repo = gh.get_repo(repo_full_name)
        # branch, commit blob, create PR ...

    This mock records all calls for testing.
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create_branch(self, repo: str, branch: str, base_sha: str) -> str:
        self.calls.append({"op": "create_branch", "repo": repo, "branch": branch})
        return base_sha  # mock returns base SHA as the branch SHA

    def create_or_update_file(
        self,
        repo: str,
        branch: str,
        path: str,
        content: str,
        message: str,
    ) -> str:
        """Returns the new blob SHA."""
        self.calls.append(
            {"op": "create_file", "repo": repo, "branch": branch, "path": path}
        )
        return hashlib.sha256(content.encode()).hexdigest()[:8]

    def create_pull_request(
        self,
        repo: str,
        head: str,
        base: str,
        title: str,
        body: str,
    ) -> tuple[int, str]:
        """Returns (pr_number, pr_url)."""
        pr_number = len(self.calls) + 1
        self.calls.append(
            {"op": "create_pr", "repo": repo, "head": head, "base": base, "title": title}
        )
        return pr_number, f"https://github.com/{repo}/pull/{pr_number}"

    def get_default_branch_sha(self, repo: str) -> str:
        self.calls.append({"op": "get_sha", "repo": repo})
        return "abc123def456"


# ── PRMaterializer ────────────────────────────────────────────


class PRMaterializer:
    """
    Enforces the Git-Mind Protocol.

    Reads a GraphDiff, compiles changed engrams to file content via CompilerDrone,
    then creates a branch and PR via PyGithub.

    No local file writes — constitutional violation otherwise.
    """

    def __init__(
        self,
        repo_full_name: str,
        base_branch: str = "main",
        github: GitHubBackend | None = None,
    ) -> None:
        self._repo = repo_full_name
        self._base = base_branch
        self._github = github or GitHubBackend()

    def materialise(
        self,
        diff: GraphDiff,
        mandate_id: UUID | None = None,
        mandate_summary: str = "",
        dry_run: bool = False,
    ) -> PRResult:
        """
        Materialise a GraphDiff as a GitHub Pull Request.

        Parameters
        ----------
        diff:             The GraphDiff produced by diff_graphs().
        mandate_id:       UUID of the originating mandate (for audit trail).
        mandate_summary:  Short NL description for the PR title.
        dry_run:          If True, compile and return artifacts but skip GitHub calls.

        Returns
        -------
        PRResult with pr_url and pr_number populated (or empty on dry_run).
        """
        mandate_id = mandate_id or uuid4()
        branch_name = f"tooloo/mandate-{str(mandate_id)[:8]}"
        result = PRResult(
            mandate_id=mandate_id,
            diff=diff,
            branch_name=branch_name,
            dry_run=dry_run,
        )

        if diff.is_empty:
            log.info("PRMaterializer: diff is empty — nothing to materialise")
            return result

        # Step 1: compile changed/added engrams to file content
        artifacts = self._compile_diff(diff)
        result.artifacts = artifacts

        if dry_run:
            log.info(
                "PRMaterializer DRY-RUN mandate=%s artifacts=%d",
                mandate_id,
                len(artifacts),
            )
            return result

        try:
            # Step 2: create branch
            base_sha = self._github.get_default_branch_sha(self._repo)
            self._github.create_branch(self._repo, branch_name, base_sha)

            # Step 3: push each compiled artifact as a file commit
            for artifact in artifacts:
                commit_msg = (
                    f"engram[{str(mandate_id)[:8]}]: update {artifact.module_path}"
                )
                self._github.create_or_update_file(
                    repo=self._repo,
                    branch=branch_name,
                    path=artifact.module_path,
                    content=artifact.content,
                    message=commit_msg,
                )

            # Step 4: open the PR
            pr_title = mandate_summary or f"TooLoo Mandate {str(mandate_id)[:8]}"
            pr_body = self._build_pr_body(diff, mandate_id, artifacts)
            pr_number, pr_url = self._github.create_pull_request(
                repo=self._repo,
                head=branch_name,
                base=self._base,
                title=pr_title,
                body=pr_body,
            )
            result.pr_number = pr_number
            result.pr_url = pr_url
            log.info(
                "PRMaterializer: PR #%d created — %s", pr_number, pr_url
            )
        except Exception as exc:  # noqa: BLE001
            result.error = str(exc)
            log.error("PRMaterializer error: %s", exc)

        return result

    def _compile_diff(self, diff: GraphDiff) -> list[CompiledArtifact]:
        """Compile all added and modified engrams to file artifacts via compile_graph()."""
        from .graph_store import EngramGraph as _EngramGraph

        artifacts: list[CompiledArtifact] = []
        module_engrams: dict[str, list[LogicEngram]] = {}

        for change in diff.engram_changes:
            if change.kind in (ChangeKind.ADDED, ChangeKind.MODIFIED) and change.after:
                eng = change.after
                module_engrams.setdefault(eng.module_path, []).append(eng)

        for module_path, engrams in module_engrams.items():
            try:
                # Build an ephemeral mini-graph for this module so compile_graph can
                # project it to source without touching the main graph.
                mini = _EngramGraph()
                for e in engrams:
                    mini.add_engram(e)
                file_map = compile_graph(mini, output_mode=OutputMode.DICT)
                content = file_map.get(module_path, "") or next(iter(file_map.values()), "")
                artifacts.append(
                    CompiledArtifact(
                        module_path=module_path,
                        content=content,
                        engram_ids=[e.engram_id for e in engrams],
                    )
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("Compile failed for %s: %s", module_path, exc)

        return artifacts

    @staticmethod
    def _build_pr_body(
        diff: GraphDiff,
        mandate_id: UUID,
        artifacts: list[CompiledArtifact],
    ) -> str:
        lines = [
            "## TooLoo Graph-Mind PR",
            "",
            f"**Mandate ID:** `{mandate_id}`",
            f"**Diff:** {diff.summary()}",
            f"**Artifacts:** {len(artifacts)} file(s)",
            "",
            "### Changed Files",
        ]
        for art in artifacts:
            engram_list = ", ".join(str(eid)[:8] for eid in art.engram_ids)
            lines.append(f"- `{art.module_path}` (engrams: {engram_list})")
        lines += [
            "",
            "---",
            "_Generated by TooLoo V2 PRMaterializer — constitutional Git-Mind Protocol_",
        ]
        return "\n".join(lines)
