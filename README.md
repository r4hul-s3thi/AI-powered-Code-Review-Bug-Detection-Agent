# AI-Powered Code Review & Bug Detection Agent

A production-ready Python agent that intercepts GitHub Pull Requests via webhooks,
parses code using Tree-sitter AST, and runs a LangGraph multi-agent pipeline to
detect security flaws, performance issues, and style violations — then posts
findings as inline PR comments.

---

## Architecture

```
GitHub PR Event
      │
      ▼
POST /webhook  (FastAPI)
      │
      ├─ HMAC-SHA256 signature verification
      ├─ Fetch unified diff + full .py sources  ──► github_client.py
      │
      ├─ AST Semantic Chunking  ──────────────────► parser.py
      │     Tree-sitter extracts only the function/class bodies
      │     that contain changed lines.
      │     Token reduction: ~95% vs raw diff approach.
      │
      └─ LangGraph Pipeline  ─────────────────────► agents.py
            │
            ├─ Orchestrator  (Send API → parallel fan-out)
            │       ├──► SecurityAgent     (OWASP Top-10)
            │       ├──► PerformanceAgent  (N+1, O(n²))
            │       └──► StyleAgent        (PEP 8, docstrings)
            │
            └─ Synthesizer  (dedup + sort High→Medium→Low)
                  │
                  └──► Post inline PR comments  ──► github_client.py
```

### Why ~95% Token Reduction?

| Approach | Tokens sent to LLM |
|---|---|
| Raw diff (500-line file) | ~5 000 tokens |
| AST chunks (2 changed functions × 15 lines) | ~300 tokens |
| **Reduction** | **94 %** |

Tree-sitter builds a full syntax tree of each file. Only `function_definition`
and `class_definition` nodes that overlap with changed lines are extracted.
The LLM receives complete, syntactically valid code units — not truncated diff
hunks — which also improves review quality.

---

## Project Structure

```
.
├── main.py            # FastAPI entrypoint + webhook handler
├── agents.py          # LangGraph state machine + all agent nodes
├── parser.py          # Tree-sitter AST semantic chunker
├── github_client.py   # GitHub API: fetch PR data, post comments
├── config.py          # Env-var loading via python-dotenv
├── test_parser.py     # Unit tests for parser
├── test_agents.py     # Unit tests for agent nodes (mocked LLM)
├── test_webhook.py    # Integration tests for FastAPI endpoints
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
└── .env.example
```

---

## Setup

### 1. Clone & install

```bash
git clone <your-repo>
cd "AI-Powered Code Review & Bug Detection Agent"
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env and fill in:
#   GITHUB_TOKEN          — Personal Access Token (repo + pull_requests scopes)
#   GITHUB_WEBHOOK_SECRET — Secret set when registering the GitHub webhook
#   OPENAI_API_KEY        — OpenAI API key
#   LLM_MODEL             — Optional: gpt-4o-mini (default) or gpt-4o
```

### 3. Run locally

```bash
uvicorn main:app --reload --port 8000
```

### 4. Expose to GitHub (development)

```bash
# Install ngrok: https://ngrok.com/download
ngrok http 8000
# Copy the https URL, e.g. https://abc123.ngrok.io
```

### 5. Register GitHub Webhook

1. Go to your repo → **Settings → Webhooks → Add webhook**
2. Payload URL: `https://abc123.ngrok.io/webhook`
3. Content type: `application/json`
4. Secret: same value as `GITHUB_WEBHOOK_SECRET` in `.env`
5. Events: select **Pull requests** only
6. Click **Add webhook**

---

## Docker

```bash
# Build and run
docker-compose up --build

# Or with plain Docker
docker build -t code-review-agent .
docker run -p 8000:8000 --env-file .env code-review-agent
```

---

## Running Tests

```bash
pytest -v
```

Tests use mocked LLM and GitHub API calls — no real credentials needed.

```
test_parser.py    — diff parsing, AST extraction, regex fallback, token reduction demo
test_agents.py    — LLM call parsing, each agent node, synthesizer dedup/sort, full graph
test_webhook.py   — signature validation, event filtering, happy path, error handling
```

---

## Output Format

Each finding is structured JSON:

```json
{
  "file": "app/views.py",
  "line": 42,
  "severity": "High",
  "issue": "SQL injection via f-string interpolation in query",
  "fix": "Use parameterised queries: db.execute('SELECT * FROM users WHERE name=?', (user,))"
}
```

Findings are sorted: **High → Medium → Low**.

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/webhook` | GitHub PR webhook receiver |
| `GET` | `/health` | Liveness probe |

---

## Rate Limiting

- `asyncio.Semaphore(5)` limits concurrent LLM calls to 5 at a time
- All blocking GitHub API calls run in a thread pool via `loop.run_in_executor`
- All findings are batched into a single `create_review()` call to respect GitHub's API limits
