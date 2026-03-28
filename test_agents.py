"""
test_agents.py — Unit tests for LangGraph agent nodes.

All LLM calls are mocked — no OpenAI API key required to run these tests.

Run with:  pytest test_agents.py -v
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Minimal chunk dict used across tests
CHUNK = {
    "file": "app/views.py",
    "name": "login",
    "kind": "function",
    "source": 'def login(user, pwd):\n    q = f"SELECT * FROM users WHERE name=\'{user}\'"\n    return db.execute(q)\n',
    "start_line": 1,
    "end_line": 3,
    "diff_lines": [2],
}

BASE_STATE = {
    "chunks": [CHUNK],
    "security_findings": [],
    "performance_findings": [],
    "style_findings": [],
    "final_findings": [],
}

SQL_FINDING = {
    "file": "app/views.py",
    "line": 2,
    "severity": "High",
    "issue": "SQL injection via f-string interpolation",
    "fix": "Use parameterised queries: db.execute('SELECT * FROM users WHERE name=?', (user,))",
}


# ── _call_llm ─────────────────────────────────────────────────────────────────
# ChatOpenAI is a frozen pydantic model — we cannot patch llm.ainvoke directly.
# Instead we create a mock LLM object and patch the module-level `llm` binding.

def _make_llm_mock(content: str) -> MagicMock:
    """Return a mock that replaces agents.llm with ainvoke returning content."""
    mock_resp = MagicMock()
    mock_resp.content = content
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=mock_resp)
    return mock_llm


@pytest.mark.asyncio
async def test_call_llm_parses_json_array():
    from agents import _call_llm
    with patch("agents.llm", _make_llm_mock(json.dumps([SQL_FINDING]))):
        result = await _call_llm("test prompt")
    assert isinstance(result, list)
    assert result[0]["severity"] == "High"


@pytest.mark.asyncio
async def test_call_llm_strips_markdown_fences():
    from agents import _call_llm
    fenced = f"```json\n{json.dumps([SQL_FINDING])}\n```"
    with patch("agents.llm", _make_llm_mock(fenced)):
        result = await _call_llm("test prompt")
    assert len(result) == 1


@pytest.mark.asyncio
async def test_call_llm_returns_empty_on_bad_json():
    from agents import _call_llm
    with patch("agents.llm", _make_llm_mock("Sorry, I cannot help.")):
        result = await _call_llm("test prompt")
    assert result == []


# ── Individual agent nodes ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_security_agent_returns_findings():
    from agents import security_agent
    with patch("agents.llm", _make_llm_mock(json.dumps([SQL_FINDING]))):
        state = await security_agent(dict(BASE_STATE))
    assert len(state["security_findings"]) == 1
    assert state["security_findings"][0]["severity"] == "High"


@pytest.mark.asyncio
async def test_performance_agent_returns_findings():
    from agents import performance_agent
    finding = {**SQL_FINDING, "severity": "Medium", "issue": "N+1 query in loop"}
    with patch("agents.llm", _make_llm_mock(json.dumps([finding]))):
        state = await performance_agent(dict(BASE_STATE))
    assert len(state["performance_findings"]) == 1


@pytest.mark.asyncio
async def test_style_agent_returns_findings():
    from agents import style_agent
    finding = {**SQL_FINDING, "severity": "Low", "issue": "Missing docstring"}
    with patch("agents.llm", _make_llm_mock(json.dumps([finding]))):
        state = await style_agent(dict(BASE_STATE))
    assert len(state["style_findings"]) == 1


# ── Synthesizer ───────────────────────────────────────────────────────────────

def test_synthesizer_deduplicates():
    from agents import synthesizer
    duplicate = dict(SQL_FINDING)
    state = {
        **BASE_STATE,
        "security_findings":    [SQL_FINDING],
        "performance_findings": [duplicate],   # same file+line+issue
        "style_findings":       [],
    }
    result = synthesizer(state)
    assert len(result["final_findings"]) == 1


def test_synthesizer_sorts_by_severity():
    from agents import synthesizer
    low    = {**SQL_FINDING, "line": 1, "severity": "Low",    "issue": "style issue"}
    medium = {**SQL_FINDING, "line": 2, "severity": "Medium", "issue": "perf issue"}
    high   = {**SQL_FINDING, "line": 3, "severity": "High",   "issue": "sql injection"}
    state = {
        **BASE_STATE,
        "security_findings":    [low],
        "performance_findings": [medium],
        "style_findings":       [high],
    }
    result = synthesizer(state)
    severities = [f["severity"] for f in result["final_findings"]]
    assert severities == ["High", "Medium", "Low"]


def test_synthesizer_handles_empty_findings():
    from agents import synthesizer
    result = synthesizer(dict(BASE_STATE))
    assert result["final_findings"] == []


def test_synthesizer_preserves_unique_findings():
    from agents import synthesizer
    f1 = {**SQL_FINDING, "line": 1, "issue": "issue A"}
    f2 = {**SQL_FINDING, "line": 2, "issue": "issue B"}
    state = {
        **BASE_STATE,
        "security_findings":    [f1],
        "performance_findings": [f2],
        "style_findings":       [],
    }
    result = synthesizer(state)
    assert len(result["final_findings"]) == 2


# ── _build_prompt ─────────────────────────────────────────────────────────────

def test_build_prompt_contains_file_and_source():
    from agents import _build_prompt
    prompt = _build_prompt([CHUNK], "security")
    assert "app/views.py" in prompt
    assert "def login" in prompt
    assert "security" in prompt


def test_build_prompt_contains_system_instruction():
    from agents import _build_prompt
    prompt = _build_prompt([CHUNK], "style")
    assert "JSON array" in prompt


# ── Full graph integration (mocked LLM) ──────────────────────────────────────

@pytest.mark.asyncio
async def test_full_graph_returns_final_findings():
    from agents import graph
    with patch("agents.llm", _make_llm_mock(json.dumps([SQL_FINDING]))):
        result = await graph.ainvoke(dict(BASE_STATE))
    assert "final_findings" in result
    assert isinstance(result["final_findings"], list)
    # Each of the 3 agents returns the same finding; after dedup → 1
    assert len(result["final_findings"]) == 1
