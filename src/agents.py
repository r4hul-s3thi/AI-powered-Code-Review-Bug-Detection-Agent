"""
agents.py — LangGraph 1.x state machine: Orchestrator → 3 Workers → Synthesizer

Graph topology (parallel fan-out via multiple add_edge from orchestrator):

    orchestrator
    ├──► security_agent   ─┐
    ├──► performance_agent ─┼──► synthesizer ──► END
    └──► style_agent      ─┘

LangGraph 1.x runs all nodes connected from the same source concurrently.
Because three workers write to the same state dict simultaneously, every
shared key must use an Annotated reducer — otherwise LastValue raises
InvalidUpdateError when it receives more than one write per step.
"""

import asyncio
import hashlib
import json
import logging
import operator
from typing import Annotated, TypedDict

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph

from src.config import LLM_MODEL, OPENAI_API_KEY

log = logging.getLogger(__name__)

# ── LLM singleton ─────────────────────────────────────────────────────────────

llm = ChatOpenAI(model=LLM_MODEL, temperature=0, api_key=OPENAI_API_KEY)

# Semaphore: max 5 concurrent LLM calls — protects against OpenAI rate limits
_RATE_LIMIT = asyncio.Semaphore(5)

# ── State schema ──────────────────────────────────────────────────────────────
# All list fields use operator.add as reducer so parallel worker writes are
# merged (concatenated) rather than overwriting each other.

class AgentState(TypedDict):
    chunks:               Annotated[list[dict], operator.add]
    security_findings:    Annotated[list[dict], operator.add]
    performance_findings: Annotated[list[dict], operator.add]
    style_findings:       Annotated[list[dict], operator.add]
    final_findings:       Annotated[list[dict], operator.add]


# ── Shared LLM prompt builder ─────────────────────────────────────────────────

_SYSTEM = (
    "You are a senior code reviewer. Analyse the Python snippet and return a "
    "JSON array of findings. Each item must be exactly: "
    '{"file": str, "line": int, "severity": "High"|"Medium"|"Low", '
    '"issue": str, "fix": str}. '
    "Return ONLY the JSON array — no markdown, no prose."
)


def _build_prompt(chunks: list[dict], focus: str) -> str:
    """
    Construct a compact LLM prompt from AST-extracted chunks.

    WHY THIS IS EFFICIENT:
      Instead of sending the entire raw diff (potentially thousands of tokens),
      we send only the function/class bodies that contain changed lines.
      A 500-line file with 2 modified functions → ~30 lines sent to the LLM.
      Token reduction: (500 - 30) / 500 ≈ 94%.  Across a PR with 10 files
      this compounds to ~95% fewer tokens vs naive raw-diff approach.
    """
    snippets = [
        f"# {c['file']}  L{c['start_line']}-{c['end_line']}  [{c['kind']}: {c['name']}]\n{c['source']}"
        for c in chunks
    ]
    return f"{_SYSTEM}\n\nFocus ONLY on: {focus}\n\n```python\n" + "\n\n".join(snippets) + "\n```"


async def _call_llm(prompt: str) -> list[dict]:
    """Invoke LLM with rate-limit guard; robustly parse JSON array response."""
    async with _RATE_LIMIT:
        resp = await llm.ainvoke([HumanMessage(content=prompt)])
    text = resp.content.strip()

    # Strip ```json ... ``` fences that some models add despite instructions
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1].lstrip("json").strip() if len(parts) > 1 else text

    try:
        data = json.loads(text)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        log.warning("LLM non-JSON response (truncated): %s", text[:300])
        return []


# ── LangGraph nodes ───────────────────────────────────────────────────────────

def orchestrator(state: AgentState) -> AgentState:
    """
    Orchestrator node: passes state through to all three worker agents.
    LangGraph 1.x runs all nodes connected from the same source concurrently.
    """
    log.info("Orchestrator dispatching %d chunks to 3 agents", len(state["chunks"]))
    # Return empty lists so the reducer doesn't double-count the initial state
    return {
        "chunks": [],
        "security_findings": [],
        "performance_findings": [],
        "style_findings": [],
        "final_findings": [],
    }


async def security_agent(state: AgentState) -> AgentState:
    """
    SecurityAgent — OWASP Top-10 focus:
      • SQL / NoSQL injection via string concatenation
      • Hardcoded secrets, API keys, passwords
      • Command injection (subprocess with shell=True, os.system)
      • Insecure deserialization (pickle.loads on untrusted data)
      • Path traversal (open() with user-controlled paths)
    """
    findings = await _call_llm(_build_prompt(
        state["chunks"],
        "OWASP security: SQL injection, hardcoded secrets/tokens/passwords, "
        "shell injection, insecure pickle/eval/exec, path traversal.",
    ))
    log.info("SecurityAgent: %d findings", len(findings))
    return {
        "chunks": [],
        "security_findings": findings,
        "performance_findings": [],
        "style_findings": [],
        "final_findings": [],
    }


async def performance_agent(state: AgentState) -> AgentState:
    """
    PerformanceAgent — efficiency focus:
      • N+1 query pattern (ORM/DB calls inside for-loops)
      • O(n²) nested loops on large collections
      • Repeated expensive calls that could be cached
      • Synchronous blocking I/O inside async functions
      • Missing bulk operations (e.g. bulk_create vs loop of .save())
    """
    findings = await _call_llm(_build_prompt(
        state["chunks"],
        "Performance: N+1 DB queries inside loops, O(n²) algorithms, "
        "repeated expensive calls, blocking I/O in async code, missing bulk ops.",
    ))
    log.info("PerformanceAgent: %d findings", len(findings))
    return {
        "chunks": [],
        "security_findings": [],
        "performance_findings": findings,
        "style_findings": [],
        "final_findings": [],
    }


async def style_agent(state: AgentState) -> AgentState:
    """
    StyleAgent — maintainability focus:
      • PEP 8 violations (naming, line length, whitespace)
      • Missing module/function/class docstrings
      • Missing type hints on public functions
      • Functions > 20 lines with no documentation
      • Magic numbers / unclear variable names
    """
    findings = await _call_llm(_build_prompt(
        state["chunks"],
        "Code style: PEP 8 violations, missing docstrings, missing type hints, "
        "magic numbers, unclear variable names, functions >20 lines undocumented.",
    ))
    log.info("StyleAgent: %d findings", len(findings))
    return {
        "chunks": [],
        "security_findings": [],
        "performance_findings": [],
        "style_findings": findings,
        "final_findings": [],
    }


def synthesizer(state: AgentState) -> AgentState:
    """
    Synthesizer node: merges all agent findings, deduplicates by content
    fingerprint, and sorts High → Medium → Low for prioritised output.
    """
    all_findings: list[dict] = (
        state.get("security_findings", [])
        + state.get("performance_findings", [])
        + state.get("style_findings", [])
    )

    seen: set[str] = set()
    deduped: list[dict] = []
    for f in all_findings:
        fp = hashlib.md5(
            f"{f.get('file')}:{f.get('line')}:{str(f.get('issue', ''))[:60]}".encode()
        ).hexdigest()
        if fp not in seen:
            seen.add(fp)
            deduped.append(f)

    _order = {"High": 0, "Medium": 1, "Low": 2}
    deduped.sort(key=lambda x: _order.get(x.get("severity", "Low"), 2))

    log.info("Synthesizer: %d unique findings after dedup", len(deduped))
    return {
        "chunks": [],
        "security_findings": [],
        "performance_findings": [],
        "style_findings": [],
        "final_findings": deduped,
    }


# ── Graph assembly ────────────────────────────────────────────────────────────

def build_graph():
    g = StateGraph(AgentState)

    g.add_node("orchestrator",      orchestrator)
    g.add_node("security_agent",    security_agent)
    g.add_node("performance_agent", performance_agent)
    g.add_node("style_agent",       style_agent)
    g.add_node("synthesizer",       synthesizer)

    g.set_entry_point("orchestrator")

    # Fan-out: orchestrator → all three workers (LangGraph runs these in parallel)
    g.add_edge("orchestrator", "security_agent")
    g.add_edge("orchestrator", "performance_agent")
    g.add_edge("orchestrator", "style_agent")

    # Fan-in: all workers → synthesizer
    g.add_edge("security_agent",    "synthesizer")
    g.add_edge("performance_agent", "synthesizer")
    g.add_edge("style_agent",       "synthesizer")

    g.add_edge("synthesizer", END)

    return g.compile()


# Compiled graph — imported by main.py
graph = build_graph()
