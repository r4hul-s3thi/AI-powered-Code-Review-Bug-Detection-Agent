"""
config.py — Centralised configuration loaded from .env
"""

import os
from dotenv import load_dotenv

load_dotenv()  # reads .env file if present

GITHUB_TOKEN: str = os.environ.get("GITHUB_TOKEN", "")
GITHUB_WEBHOOK_SECRET: str = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
OPENAI_API_KEY: str = os.environ.get("OPENAI_API_KEY", "")
LLM_MODEL: str = os.environ.get("LLM_MODEL", "gpt-4o-mini")

# Safety guard — warn loudly at startup if critical vars are missing
import logging
_log = logging.getLogger(__name__)
if not GITHUB_TOKEN:
    _log.warning("GITHUB_TOKEN is not set — GitHub API calls will fail")
if not OPENAI_API_KEY:
    _log.warning("OPENAI_API_KEY is not set — LLM calls will fail")
