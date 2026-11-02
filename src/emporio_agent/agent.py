"""Fachada do agente: roda o grafo (graph.py) e guarda cada conversa num checkpoint SQLite.

`stream()` devolve os eventos do turno à medida que acontecem (dicts com
"type": step, route, retrieval, llm, tool, waiting, check, reply...). A
interface web repassa esses eventos por SSE; o CLI e o gerador de conversas
usam os mesmos eventos. `send()` é o atalho que só devolve a resposta final.
"""

import re
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path

import groq
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Overwrite

from . import config
from .graph import build_graph
from .rag import PolicyIndex, load_index
from .tools import ToolExecutor

# O ID vai para a URL e para o checkpoint, então só aceita caracteres seguros.
SESSION_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}")
# Limite de passos do LangGraph (o padrão é alto demais para proteger de laços).
RECURSION_LIMIT = 40

_saver_lock = threading.Lock()


@lru_cache(maxsize=4)
def _saver(path: Path) -> SqliteSaver:
    path.parent.mkdir(parents=True, exist_ok=True)
    return SqliteSaver(sqlite3.connect(path, check_same_thread=False))


def default_checkpointer() -> SqliteSaver:
    with _saver_lock:
        return _saver(config.SESSIONS_DIR / "conversas.sqlite")


class Agent:
    def __init__(
        self,
        session_id: str | None = None,
        client: groq.Groq | None = None,
        checkpointer=None,
        index: PolicyIndex | None = None,
    ):
        if session_id is not None and not SESSION_ID_PATTERN.fullmatch(session_id):
            raise ValueError(f"ID de sessão inválido: {session_id!r}")
        self.session_id = session_id or uuid.uuid4().hex[:8]
        # Sem retentativas no SDK: as esperas por limite de taxa são feitas no
        # grafo, para poderem ser avisadas ao usuário.
        client = client or groq.Groq(api_key=config.API_KEY, max_retries=0)
        self.index = index or load_index()
        self.checkpointer = checkpointer or default_checkpointer()
        self.graph = build_graph(client, ToolExecutor(), self.index).compile(checkpointer=self.checkpointer)
        self.config = {"configurable": {"thread_id": self.session_id}, "recursion_limit": RECURSION_LIMIT}

    @property
    def state(self) -> dict:
        return self.graph.get_state(self.config).values

    @property
    def messages(self) -> list[dict]:
        return self.state.get("messages", [])

    @property
    def log(self) -> list[dict]:
        return self.state.get("log", [])

    @property
    def exists(self) -> bool:
        return bool(self.messages)

    def stream(self, user_text: str) -> Iterator[dict]:
        """Processa uma mensagem do cliente, devolvendo eventos até a resposta final."""
        before = self.messages
        events: list[dict] = []
        try:
            for event in self.graph.stream({"turn": {"raw": user_text}}, self.config, stream_mode="custom"):
                events.append(event)
                yield event
        except BaseException:
            # Turno incompleto não fica no histórico: a próxima tentativa começa
            # de um estado consistente.
            self.graph.update_state(self.config, {"messages": Overwrite(before)})
            raise
        self.graph.update_state(self.config, {"log": [{"events": events}]})

    def send(self, user_text: str) -> dict:
        reply = None
        for event in self.stream(user_text):
            if event["type"] == "reply":
                reply = event
        return reply
