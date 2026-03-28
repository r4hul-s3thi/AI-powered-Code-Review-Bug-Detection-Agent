"""
test_webhook.py — Integration tests for FastAPI webhook endpoint.
All external calls (GitHub API, LLM) are mocked.
"""

import hashlib
import hmac
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.main import app

client = TestClient(app)


def _sign(payload: bytes, secret: str = "test-secret") -> str:
    return "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


PR_EVENT = {
    "action": "opened",
    "repository": {"full_name": "owner/repo"},
    "pull_request": {"number": 42, "head": {"sha": "abc123def456"}},
}

FINDING = {
    "file": "app/views.py",
    "line": 5,
    "severity": "High",
    "issue": "SQL injection",
    "fix": "Use parameterised queries",
}


def test_health_returns_ok():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert "tree_sitter_available" in resp.json()


def test_webhook_rejects_bad_signature():
    with patch("src.main.verify_signature", return_value=False):
        resp = client.post("/webhook", content=b"{}", headers={"x-github-event": "pull_request", "x-hub-signature-256": "sha256=bad"})
    assert resp.status_code == 401


def test_webhook_ignores_non_pr_events():
    with patch("src.main.verify_signature", return_value=True):
        resp = client.post("/webhook", content=b"{}", headers={"x-github-event": "push"})
    assert resp.json()["status"] == "ignored"


def test_webhook_ignores_closed_action():
    payload = json.dumps({**PR_EVENT, "action": "closed"}).encode()
    with patch("src.main.verify_signature", return_value=True):
        resp = client.post("/webhook", content=payload, headers={"x-github-event": "pull_request"})
    assert resp.json()["status"] == "ignored"


def test_webhook_processes_opened_pr():
    payload = json.dumps(PR_EVENT).encode()
    mock_chunk = MagicMock()
    mock_chunk.file = "app/views.py"
    mock_chunk.name = "login"
    mock_chunk.kind = "function"
    mock_chunk.source = "def login(): pass"
    mock_chunk.start_line = 1
    mock_chunk.end_line = 1
    mock_chunk.diff_lines = [1]

    graph_result = {"chunks": [], "security_findings": [], "performance_findings": [], "style_findings": [], "final_findings": [FINDING]}

    with (
        patch("src.main.verify_signature", return_value=True),
        patch("src.main.fetch_pr_data", return_value=("diff text", {"app/views.py": "def login(): pass"})),
        patch("src.main.extract_chunks_from_diff", return_value=[mock_chunk]),
        patch("src.main.graph.ainvoke", new=AsyncMock(return_value=graph_result)),
        patch("src.main.post_review_comments"),
    ):
        resp = client.post("/webhook", content=payload, headers={"x-github-event": "pull_request"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["findings_count"] == 1
    assert body["findings"][0]["severity"] == "High"


def test_webhook_returns_no_python_changes_when_no_chunks():
    payload = json.dumps(PR_EVENT).encode()
    with (
        patch("src.main.verify_signature", return_value=True),
        patch("src.main.fetch_pr_data", return_value=("", {})),
        patch("src.main.extract_chunks_from_diff", return_value=[]),
    ):
        resp = client.post("/webhook", content=payload, headers={"x-github-event": "pull_request"})
    assert resp.json()["status"] == "no_python_changes"


def test_webhook_handles_github_api_error():
    payload = json.dumps(PR_EVENT).encode()
    with (
        patch("src.main.verify_signature", return_value=True),
        patch("src.main.fetch_pr_data", side_effect=Exception("GitHub 403")),
    ):
        resp = client.post("/webhook", content=payload, headers={"x-github-event": "pull_request"})
    assert resp.status_code == 502
