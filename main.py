"""
main.py — FastAPI entrypoint for the AI-Powered Code Review Agent.

Request lifecycle:
  POST /webhook  (GitHub PR event)
      │
      ├─ verify HMAC signature          [github_client.verify_signature]
      ├─ fetch diff + file sources      [github_client.fetch_pr_data]        (thread pool)
      ├─ AST semantic chunking          [parser.extract_chunks_from_diff]
      │     └─ ~95% token reduction: only changed function/class bodies sent to LLM
      ├─ LangGraph pipeline             [agents.graph.ainvoke]
      │     ├─ orchestrator  (fan-out via Send API)
      │     ├─ security_agent  ──┐
      │     ├─ performance_agent ├─ parallel LLM calls
      │     ├─ style_agent    ──┘
      │     └─ synthesizer   (dedup + sort by severity)
      └─ post inline PR comments        [github_client.post_review_comments] (thread pool)
"""

import asyncio
import logging

from fastapi import FastAPI, Header, HTTPException, Request
from typing import Annotated

import config  # noqa: F401 — triggers dotenv load and startup warnings
from agents import AgentState, graph
from github_client import fetch_pr_data, post_review_comments, verify_signature
from parser import extract_chunks_from_diff

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
)
log = logging.getLogger(__name__)

app = FastAPI(
    title="AI Code Review Agent",
    description="GitHub PR webhook → AST chunking → LangGraph multi-agent review",
    version="1.0.0",
)


@app.get("/health")
async def health():
    """Liveness probe — also reports whether tree-sitter loaded successfully."""
    from parser import TREE_SITTER_AVAILABLE
    return {"status": "ok", "tree_sitter_available": TREE_SITTER_AVAILABLE}


@app.post("/webhook")
async def webhook(
    request: Request,
    x_hub_signature_256: Annotated[str | None, Header()] = None,
    x_github_event: Annotated[str | None, Header()] = None,
):
    """
    Receives GitHub webhook events.
    Only processes pull_request events with actions: opened / synchronize / reopened.
    """
    payload_bytes = await request.body()

    # ── 1. Verify webhook signature ───────────────────────────────────────────
    if not verify_signature(payload_bytes, x_hub_signature_256 or ""):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    # ── 2. Filter event type ──────────────────────────────────────────────────
    if x_github_event != "pull_request":
        return {"status": "ignored", "reason": f"event={x_github_event}"}

    event: dict = await request.json()
    action: str = event.get("action", "")
    if action not in ("opened", "synchronize", "reopened"):
        return {"status": "ignored", "reason": f"action={action}"}

    pr_data        = event["pull_request"]
    repo_full_name: str = event["repository"]["full_name"]
    pr_number: int      = pr_data["number"]
    commit_sha: str     = pr_data["head"]["sha"]

    log.info("▶ PR #%d on %s  (action=%s)", pr_number, repo_full_name, action)

    loop = asyncio.get_event_loop()

    # ── 3. Fetch diff + file sources (blocking I/O → thread pool) ────────────
    try:
        diff_text, file_contents = await loop.run_in_executor(
            None, fetch_pr_data, repo_full_name, pr_number
        )
    except Exception as exc:
        log.error("Failed to fetch PR data: %s", exc)
        raise HTTPException(status_code=502, detail=f"GitHub API error: {exc}")

    # ── 4. AST semantic chunking ──────────────────────────────────────────────
    #   This is the core token-reduction step. Instead of forwarding the entire
    #   raw diff to the LLM, we extract only the function/class bodies that
    #   contain changed lines. Typical reduction: 94-96% fewer tokens.
    chunks = extract_chunks_from_diff(diff_text, file_contents)
    if not chunks:
        log.info("No Python chunks found in PR #%d — skipping review", pr_number)
        return {"status": "no_python_changes", "pr": pr_number}

    log.info("Extracted %d semantic chunks from PR #%d", len(chunks), pr_number)

    # ── 5. LangGraph multi-agent pipeline ────────────────────────────────────
    initial_state: AgentState = {
        "chunks": [
            {
                "file":       c.file,
                "name":       c.name,
                "kind":       c.kind,
                "source":     c.source,
                "start_line": c.start_line,
                "end_line":   c.end_line,
                "diff_lines": c.diff_lines,
            }
            for c in chunks
        ],
        "security_findings":    [],
        "performance_findings": [],
        "style_findings":       [],
        "final_findings":       [],
    }

    try:
        result = await graph.ainvoke(initial_state)
    except Exception as exc:
        log.error("LangGraph pipeline failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Agent pipeline error: {exc}")

    findings: list[dict] = result["final_findings"]
    log.info("Pipeline complete — %d findings for PR #%d", len(findings), pr_number)

    # ── 6. Post inline PR comments (blocking I/O → thread pool) ──────────────
    if findings:
        await loop.run_in_executor(
            None, post_review_comments, repo_full_name, pr_number, findings, commit_sha
        )

    return {
        "status":         "ok",
        "pr":             pr_number,
        "chunks_analysed": len(chunks),
        "findings_count": len(findings),
        "findings":       findings,
    }
