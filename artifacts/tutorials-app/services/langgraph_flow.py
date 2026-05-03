"""
LangGraph orchestration for the tutorial generation pipeline.

Flow
----
brainstorm → prd → spec → writer → reviewer → fixer
                                      ↑            │
                                      └────────────┘
                                   (max 2 revision cycles)

After fixer:
  • If review status is APPROVED, or revision_count >= MAX_REVISIONS → END
  • Otherwise → back to reviewer for another cycle (max 2 total)

State fields (TypedDict)
------------------------
messages              list[str]  — append-only log of pipeline events
title                 str        — tutorial title (set by prd_agent)
technology            str        — technology input by the user
source_documents_text str        — merged text from uploaded docs
requirements          str        — raw requirements/context from user
target_audience       str
technical_level       str
objective             str
operating_environment str
prerequisites         list[str]
depth                 str
practical_examples    dict
common_errors         list[str]
expected_outcome      str
chat_history          list[dict] — brainstorm conversation turns
brainstorm            dict       — output of brainstorm_agent
prd                   dict       — output of prd_agent
spec                  dict       — output of spec_agent
draft                 str        — current working draft (updated each fixer cycle)
review                dict       — last reviewer output
final                 str        — final approved Markdown tutorial
revision_count        int        — number of fixer→reviewer cycles completed
status                str        — pipeline stage label
errors                list[str]  — accumulated error messages
"""

from __future__ import annotations

import logging
import operator
from datetime import datetime, timezone
from typing import Annotated, Any

from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

from services.agents import (
    brainstorm_agent,
    fixer_agent,
    prd_agent,
    reviewer_agent,
    spec_agent,
    writer_agent,
)

logger = logging.getLogger(__name__)

MAX_REVISIONS: int = 2


# ---------------------------------------------------------------------------
# State definition
# ---------------------------------------------------------------------------

class TutorialState(TypedDict, total=False):
    # Append-only event log — LangGraph merges with operator.add
    messages: Annotated[list[str], operator.add]

    # User inputs
    title: str
    technology: str
    source_documents_text: str
    requirements: str
    target_audience: str
    technical_level: str
    objective: str
    operating_environment: str
    prerequisites: list[str]
    depth: str
    practical_examples: dict
    common_errors: list[str]
    expected_outcome: str
    chat_history: list[dict]

    # Agent outputs
    brainstorm: dict
    prd: dict
    spec: dict
    draft: str           # current working draft (updated by writer + fixer)
    review: dict         # latest reviewer report
    final: str           # final approved tutorial

    # Control
    revision_count: int
    status: str
    errors: list[str]
    ai_mode: str   # "balanced" | "economic" | "quality"


# ---------------------------------------------------------------------------
# State ↔ Agent adapters
#
# The agent functions use internal field names (draft_content, final_content_md).
# These thin wrappers translate in/out so the TypedDict stays clean.
# ---------------------------------------------------------------------------

def _to_agent_state(state: dict) -> dict:
    """Map TypedDict fields → agent dict expected by services/agents.py."""
    s = dict(state)
    # Map graph "draft" → agent "draft_content"
    if "draft" in s:
        s.setdefault("draft_content", s["draft"])
    # Map graph "final" → agent "final_content_md"
    if "final" in s:
        s.setdefault("final_content_md", s["final"])
    s.setdefault("errors", [])
    s.setdefault("ai_mode", "balanced")
    return s


def _from_agent_state(agent_out: dict, current: dict) -> dict:
    """Extract updated fields from agent output and map back to TypedDict keys."""
    delta: dict[str, Any] = {}

    # Scalar agent outputs
    for key in ("brainstorm", "prd", "spec", "review", "status"):
        if key in agent_out:
            delta[key] = agent_out[key]

    # draft_content → draft
    if "draft_content" in agent_out and agent_out["draft_content"]:
        delta["draft"] = agent_out["draft_content"]

    # final_content_md → final
    if "final_content_md" in agent_out and agent_out["final_content_md"]:
        delta["final"] = agent_out["final_content_md"]

    # title from prd
    if "prd" in agent_out and isinstance(agent_out["prd"], dict):
        delta["title"] = agent_out["prd"].get("title", current.get("title", ""))

    # Accumulate errors
    new_errors = agent_out.get("errors", [])
    if new_errors:
        existing = list(current.get("errors", []))
        delta["errors"] = existing + new_errors

    return delta


def _log(message: str) -> dict:
    """Return a partial state update that appends one message to the log."""
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    return {"messages": [f"[{ts}] {message}"]}


# ---------------------------------------------------------------------------
# Node functions
# ---------------------------------------------------------------------------

def node_brainstorm(state: TutorialState) -> dict:
    logger.info("▶ node_brainstorm")
    agent_out = brainstorm_agent(_to_agent_state(state))
    delta = _from_agent_state(agent_out, state)
    delta.update(_log("Brainstorm concluído"))
    delta["status"] = "brainstorm_done"
    return delta


def node_prd(state: TutorialState) -> dict:
    logger.info("▶ node_prd")
    agent_out = prd_agent(_to_agent_state(state))
    delta = _from_agent_state(agent_out, state)
    delta.update(_log(f"PRD gerado: {delta.get('title', '')}"))
    delta["status"] = "prd_done"
    return delta


def node_spec(state: TutorialState) -> dict:
    logger.info("▶ node_spec")
    agent_out = spec_agent(_to_agent_state(state))
    delta = _from_agent_state(agent_out, state)
    sections = len((delta.get("spec") or {}).get("sections", []))
    delta.update(_log(f"Spec gerada: {sections} seções"))
    delta["status"] = "spec_done"
    return delta


def node_writer(state: TutorialState) -> dict:
    logger.info("▶ node_writer")
    # Feed the current draft back into draft_content for rewrite cycles
    agent_in = _to_agent_state(state)
    agent_out = writer_agent(agent_in)
    delta = _from_agent_state(agent_out, state)
    chars = len(delta.get("draft", ""))
    delta.update(_log(f"Rascunho escrito: {chars:,} caracteres"))
    delta["status"] = "writer_done"
    return delta


def node_reviewer(state: TutorialState) -> dict:
    logger.info("▶ node_reviewer")
    agent_out = reviewer_agent(_to_agent_state(state))
    delta = _from_agent_state(agent_out, state)
    review = delta.get("review") or {}
    delta.update(_log(
        f"Revisão: {review.get('status', '?')} — "
        f"score {review.get('overall_score', 0):.1f} — "
        f"{len(review.get('issues', []))} issue(s)"
    ))
    delta["status"] = "reviewer_done"
    return delta


def node_fixer(state: TutorialState) -> dict:
    logger.info("▶ node_fixer (ciclo %d)", state.get("revision_count", 0) + 1)
    agent_in = _to_agent_state(state)
    # The fixer reads draft_content; feed the current working draft
    agent_in["draft_content"] = state.get("draft", "")
    agent_out = fixer_agent(agent_in)
    delta = _from_agent_state(agent_out, state)

    # Increment revision counter
    new_count = state.get("revision_count", 0) + 1
    delta["revision_count"] = new_count

    # After fixing, promote the final as the new working draft
    # so the reviewer sees the improved version on the next cycle
    if delta.get("final"):
        delta["draft"] = delta["final"]

    review_status = (state.get("review") or {}).get("status", "NEEDS_REVISION")
    if review_status == "APPROVED" or new_count >= MAX_REVISIONS:
        delta.update(_log(f"Tutorial finalizado após {new_count} ciclo(s) de revisão"))
        delta["status"] = "complete"
    else:
        delta.update(_log(f"Correção aplicada (ciclo {new_count}) — enviando para re-revisão"))
        delta["status"] = "fixer_done"

    return delta


# ---------------------------------------------------------------------------
# Routing functions (conditional edges)
# ---------------------------------------------------------------------------

def route_after_fixer(state: TutorialState) -> str:
    """
    After fixer runs:
    • If tutorial is approved OR max revisions reached → END
    • Otherwise → reviewer for another cycle
    """
    review_status = (state.get("review") or {}).get("status", "NEEDS_REVISION")
    revision_count = state.get("revision_count", 0)

    if review_status == "APPROVED" or revision_count >= MAX_REVISIONS:
        logger.info("route_after_fixer → END (status=%s, cycles=%d)", review_status, revision_count)
        return END

    logger.info("route_after_fixer → reviewer (cycle %d/%d)", revision_count, MAX_REVISIONS)
    return "reviewer"


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------

def build_tutorial_graph() -> Any:
    """
    Build and compile the LangGraph StateGraph for tutorial generation.

    Returns the compiled graph, ready to be invoked with an initial state.
    """
    graph = StateGraph(TutorialState)

    # Register nodes
    graph.add_node("brainstorm", node_brainstorm)
    graph.add_node("prd", node_prd)
    graph.add_node("spec", node_spec)
    graph.add_node("writer", node_writer)
    graph.add_node("reviewer", node_reviewer)
    graph.add_node("fixer", node_fixer)

    # Linear flow: brainstorm → prd → spec → writer → reviewer → fixer
    graph.set_entry_point("brainstorm")
    graph.add_edge("brainstorm", "prd")
    graph.add_edge("prd", "spec")
    graph.add_edge("spec", "writer")
    graph.add_edge("writer", "reviewer")
    graph.add_edge("reviewer", "fixer")

    # Conditional edge from fixer: loop or finish
    graph.add_conditional_edges(
        "fixer",
        route_after_fixer,
        {
            "reviewer": "reviewer",  # revision loop
            END: END,                # done
        },
    )

    compiled = graph.compile()
    logger.info("Tutorial graph compiled successfully.")
    return compiled


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

# Module-level compiled graph (built once, reused across calls)
_GRAPH = None


def _get_graph() -> Any:
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_tutorial_graph()
    return _GRAPH


def run_tutorial_graph(initial_state: dict) -> dict:
    """
    Execute the full tutorial generation pipeline and return the final state.

    The function accepts a plain dict with any subset of TutorialState fields.
    Missing fields are initialised to safe defaults before the graph runs.

    Args:
        initial_state: Dict with at least ``technology`` set.
                       Optionally: target_audience, technical_level, objective,
                       operating_environment, prerequisites, depth,
                       practical_examples, common_errors, expected_outcome,
                       source_documents_text, requirements, chat_history.

    Returns:
        The final TutorialState dict after all agents have run.
        Key fields of interest:
          • ``final``         — complete Markdown tutorial (ready to save/export)
          • ``draft``         — last working draft
          • ``review``        — last reviewer report
          • ``messages``      — full event log
          • ``revision_count``— number of fixer→reviewer cycles
          • ``status``        — "complete" on success
          • ``errors``        — list of error messages (empty on clean run)

    Raises:
        ValueError: If ``technology`` is not provided in initial_state.
        Exception:  Re-raises unexpected LangGraph execution errors.
    """
    tech = (initial_state.get("technology") or "").strip()
    if not tech:
        raise ValueError("O campo 'technology' é obrigatório para iniciar o pipeline.")

    # Build a complete initial state with safe defaults
    safe_state: dict = {
        "messages": [f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] Pipeline iniciado para: {tech}"],
        "title": "",
        "technology": tech,
        "source_documents_text": initial_state.get("source_documents_text") or "",
        "requirements": initial_state.get("requirements") or "",
        "target_audience": initial_state.get("target_audience") or "desenvolvedores",
        "technical_level": initial_state.get("technical_level") or "intermediário",
        "objective": initial_state.get("objective") or f"Aprender {tech} na prática",
        "operating_environment": initial_state.get("operating_environment") or "Linux / macOS / Windows",
        "prerequisites": initial_state.get("prerequisites") or [],
        "depth": initial_state.get("depth") or "completo",
        "practical_examples": initial_state.get("practical_examples") or {"include": True, "count": 3, "description": ""},
        "common_errors": initial_state.get("common_errors") or [],
        "expected_outcome": initial_state.get("expected_outcome") or f"Domínio prático de {tech}",
        "chat_history": initial_state.get("chat_history") or [],
        "brainstorm": {},
        "prd": {},
        "spec": {},
        "draft": "",
        "review": {},
        "final": "",
        "revision_count": 0,
        "status": "pending",
        "errors": [],
    }

    logger.info("run_tutorial_graph: starting pipeline for technology=%r", tech)

    try:
        graph = _get_graph()
        final_state = graph.invoke(safe_state)
        logger.info(
            "run_tutorial_graph: completed — status=%s revisions=%d final_chars=%d",
            final_state.get("status"),
            final_state.get("revision_count", 0),
            len(final_state.get("final", "")),
        )
        return final_state
    except Exception as exc:
        logger.error("run_tutorial_graph: pipeline failed — %s", exc)
        safe_state["status"] = "error"
        safe_state["errors"].append(str(exc))
        raise


def run_tutorial_flow(topic: str) -> str:
    """
    Convenience wrapper: run the pipeline for a topic string and return
    the final Markdown tutorial.

    Args:
        topic: Technology or subject (e.g. "Docker", "FastAPI com JWT").

    Returns:
        Final tutorial as a Markdown string.
    """
    state = run_tutorial_graph({"technology": topic})
    return state.get("final") or state.get("draft") or ""
