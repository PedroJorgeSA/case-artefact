"""Testes da API web com um agente falso (sem chamar o Groq)."""

import json

import groq
import httpx
import pytest
from fastapi.testclient import TestClient

from emporio_agent import config
from emporio_agent.agent import Reply, ToolCall, Waiting
from emporio_agent.web import _history_items, create_app


class FakeAgent:
    def __init__(self, session_id=None, events=None):
        self.session_id = session_id or "abc12345"
        self.messages = []
        self.events = events

    def stream(self, text):
        self.messages.append({"role": "user", "content": text})
        yield Waiting(2.0)
        yield ToolCall("buscar_produtos", {"termo": "violão"}, json.dumps({"total_encontrado": 0, "produtos": []}), False)
        yield Reply("Não encontrei esse modelo.")


def _events(response) -> list[dict]:
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


@pytest.fixture
def client():
    return TestClient(create_app(agent_factory=FakeAgent))


def test_chat_streams_events_in_order(client):
    response = client.post("/api/chat", json={"message": "tem violão?"})
    assert response.status_code == 200
    events = _events(response)
    assert [e["type"] for e in events] == ["session", "waiting", "tool", "reply"]
    assert events[0]["session_id"] == "abc12345"
    assert events[2]["result"] == {"total_encontrado": 0, "produtos": []}
    assert events[3]["text"] == "Não encontrei esse modelo."


def test_rejects_unsafe_session_id(client):
    response = client.post("/api/chat", json={"message": "oi", "session_id": "../../etc"})
    assert response.status_code == 422


def test_api_errors_become_events():
    class BrokenAgent(FakeAgent):
        def stream(self, text):
            response = httpx.Response(401, request=httpx.Request("POST", "https://groq.test"))
            raise groq.AuthenticationError("bad key", response=response, body=None)
            yield  # noqa: unreachable — torna o método um gerador

    client = TestClient(create_app(agent_factory=BrokenAgent))
    events = _events(client.post("/api/chat", json={"message": "oi"}))
    assert events[-1]["type"] == "error" and "GROQ_API_KEY" in events[-1]["message"]


def test_unknown_session_is_404(client, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SESSIONS_DIR", tmp_path)
    assert client.get("/api/sessions/naoexiste").status_code == 404


def test_history_pairs_tool_calls_with_results():
    messages = [
        {"role": "user", "content": "pedido 8"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "c1", "function": {"name": "consultar_pedido", "arguments": '{"numero_pedido": 8}'}}],
        },
        {"role": "tool", "tool_call_id": "c1", "content": '{"erro": "não confere"}'},
        {"role": "assistant", "content": "Não consegui confirmar."},
    ]
    items = _history_items(messages)
    assert [i["type"] for i in items] == ["user", "tool", "reply"]
    assert items[1]["is_error"] and items[1]["args"] == {"numero_pedido": 8}


def test_info_reports_store_clock(client):
    info = client.get("/api/info").json()
    assert {"modelo", "agora", "loja_aberta", "status"} <= info.keys()
