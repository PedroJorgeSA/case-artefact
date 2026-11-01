"""Configuração lida do ambiente (.env) e caminhos do projeto."""

import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / "data"
KNOWLEDGE_DIR = ROOT_DIR / "knowledge"
SESSIONS_DIR = ROOT_DIR / ".sessions"
HANDOFFS_DIR = ROOT_DIR / ".handoffs"

load_dotenv(ROOT_DIR / ".env")

# Campo Grande/MS não tem horário de verão: UTC-4 o ano todo.
STORE_TZ = ZoneInfo("America/Campo_Grande")

API_KEY = os.getenv("GROQ_API_KEY")
MODEL = os.getenv("EMPORIO_MODEL") or "openai/gpt-oss-120b"
# Quanto o modelo "pensa" antes de responder: low | medium | high.
EFFORT = os.getenv("EMPORIO_EFFORT") or "medium"
MAX_TOOL_ITERATIONS = 8


def now() -> datetime:
    """Data/hora de referência do agente.

    EMPORIO_NOW permite fixar o relógio (ex.: 2026-03-25T10:00), o que deixa as
    conversas de exemplo reproduzíveis, já que os dados do case param em mar/2026.
    """
    fixed = os.getenv("EMPORIO_NOW", "").strip()
    if fixed:
        parsed = datetime.fromisoformat(fixed)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=STORE_TZ)
    return datetime.now(STORE_TZ)
