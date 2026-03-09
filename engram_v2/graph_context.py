"""
Graph Context — ContextTensor assembly from EngramGraph.

Replaces ContextAssembler for Graph-native context injection.
Instead of reading files + Hippocampus, reads graph topology
and assembles a compressed, targeted context for each Mitosis clone.
"""

from __future__ import annotations

import contextlib
import json
from typing import TYPE_CHECKING
from uuid import UUID

from .schema import ContextTensor

if TYPE_CHECKING:
    from .graph_store import EngramGraph


def assemble_tensor(
    graph: EngramGraph,
    target_engram_ids: list[UUID],
    mandate_text: str,
    token_budget: int = 8000,
) -> ContextTensor:
    """Assemble a ContextTensor for a specific set of target Engrams.

    Only includes the LOCAL subgraph relevant to the targets,
    not the entire codebase. This is the key token-saving mechanism.
    """
    # Collect dependency subgraphs for all targets
    all_relevant_ids: set[UUID] = set(target_engram_ids)
    intent_chain: list[str] = [mandate_text]

    for eid in target_engram_ids:
        sub = graph.get_dependency_subgraph(eid, depth=2)
        for node_id_str in sub.nodes():
            with contextlib.suppress(ValueError):
                all_relevant_ids.add(UUID(node_id_str))

    # Build subgraph JSON (intents + edges only for token efficiency)
    subgraph_data: dict = {"engrams": [], "edges": []}
    for rid in all_relevant_ids:
        eng = graph.get_engram(rid)
        if eng:
            intent_chain.append(eng.intent)
            subgraph_data["engrams"].append(
                {
                    "id": str(eng.engram_id),
                    "intent": eng.intent,
                    "sig": eng.ast_signature,
                    "module": eng.module_path,
                    "domain": eng.domain.value,
                }
            )

    # Only include edges within the subgraph
    for edge in graph._edges.values():
        if edge.source_id in all_relevant_ids and edge.target_id in all_relevant_ids:
            subgraph_data["edges"].append(
                {
                    "src": str(edge.source_id),
                    "tgt": str(edge.target_id),
                    "type": edge.edge_type.value,
                }
            )

    subgraph_json = json.dumps(subgraph_data, indent=1)

    # Assemble the prompt
    prompt_parts: list[str] = [
        "<graph_context>",
        f"<mandate>{mandate_text}</mandate>",
        "<target_engrams>",
    ]
    for eid in target_engram_ids:
        eng = graph.get_engram(eid)
        if eng:
            prompt_parts.append(
                f'  <target id="{eng.engram_id}" '
                f'intent="{eng.intent}" '
                f'sig="{eng.ast_signature}" '
                f'module="{eng.module_path}" />'
            )
    prompt_parts.append("</target_engrams>")

    # Add dependency context (signatures only, no full bodies)
    dep_ids = all_relevant_ids - set(target_engram_ids)
    if dep_ids:
        prompt_parts.append("<dependencies>")
        for did in dep_ids:
            dep = graph.get_engram(did)
            if dep:
                prompt_parts.append(
                    f'  <dep id="{dep.engram_id}" '
                    f'intent="{dep.intent}" '
                    f'sig="{dep.ast_signature}" '
                    f'module="{dep.module_path}" />'
                )
        prompt_parts.append("</dependencies>")

    # Add edge topology
    if subgraph_data["edges"]:
        prompt_parts.append("<edges>")
        for e in subgraph_data["edges"]:
            prompt_parts.append(f'  <edge src="{e["src"]}" tgt="{e["tgt"]}" type="{e["type"]}" />')
        prompt_parts.append("</edges>")

    prompt_parts.append("</graph_context>")
    assembled = "\n".join(prompt_parts)

    # Truncate if over budget
    if len(assembled) // 4 > token_budget:
        assembled = assembled[: token_budget * 4]

    return ContextTensor(
        target_engrams=target_engram_ids,
        dependency_subgraph_json=subgraph_json,
        intent_chain=intent_chain,
        token_budget=token_budget,
        assembled_prompt=assembled,
    )


def assemble_full_graph_context(
    graph: EngramGraph,
    mandate_text: str,
    token_budget: int = 12000,
) -> str:
    """Assemble context for the PLANNING step (before decomposition).

    Uses the graph's token_summary (topology only) instead of full file contents.
    """
    topology = graph.to_token_summary()
    stats = graph.stats()

    parts = [
        "<planning_context>",
        f"<mandate>{mandate_text}</mandate>",
        f'<graph_stats nodes="{stats["nodes"]}" edges="{stats["edges"]}" '
        f'depth="{stats["max_depth"]}" components="{stats["connected_components"]}" />',
        topology,
        "</planning_context>",
    ]

    assembled = "\n".join(parts)
    if len(assembled) // 4 > token_budget:
        assembled = assembled[: token_budget * 4]
    return assembled
