"""Testes da API web com um agente falso (sem chamar o Groq)."""

import json

import groq
import httpx
import pytest
from fastapi.testclient import TestClient

from emporio_agent import config
from emporio_agent.web import create_app


class FakeAgent:
    def __init__(self, session_id=None):
        self.session_id = session_id or "abc12345"
        self.log = []

    @property
    def exists(self):
        return bool(self.log)

    def stream(self, text):
        events = [
            {"type": "user", "text": text},
            {"type": "waiting", "seconds": 2.0},
            {"type": "tool", "name": "buscar_produtos", "args": {"termo": "violão"}, "result": {"total_encontrado": 0}, "is_error": False},
            {"type": "reply", "text": "Não encontrei esse modelo.", "citations": [], "options": [], "tools": ["buscar_produtos"]},
        ]
        yield from events
        self.log.append({"events": events})


def _events(response) -> list[dict]:
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


@pytest.fixture
def client():
    return TestClient(create_app(agent_factory=FakeAgent))


def test_chat_streams_events_in_order(client):
    response = client.post("/api/chat", json={"message": "tem violão?"})
    assert response.status_code == 200
    events = _events(response)
    assert [e["type"] for e in events] == ["session", "user", "waiting", "tool", "reply"]
    assert events[0]["session_id"] == "abc12345"
    assert events[3]["result"] == {"total_encontrado": 0}
    assert events[4]["text"] == "Não encontrei esse modelo."


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


def test_unknown_session_is_404(client):
    assert client.get("/api/sessions/naoexiste").status_code == 404


def test_history_replays_the_saved_events(client):
    client.post("/api/chat", json={"message": "tem violão?", "session_id": "s1"})
    turns = client.get("/api/sessions/s1").json()["turns"]
    assert len(turns) == 1 and turns[0][0] == {"type": "user", "text": "tem violão?"}


def test_feedback_is_recorded(client, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "FEEDBACK_DIR", tmp_path)
    response = client.post("/api/feedback", json={"session_id": "s1", "turn": 0, "rating": "down", "reason": "preço errado"})
    assert response.json() == {"ok": True}
    assert "preço errado" in (tmp_path / "avaliacoes.jsonl").read_text(encoding="utf-8")


def test_scenarios_are_listed_and_checked(client):
    scenarios = client.get("/api/scenarios").json()
    assert len(scenarios) == 5 and scenarios[0]["mensagens"]
    replies = [{"text": "ok", "tools": ["buscar_produtos"], "citations": []}]
    checks = client.post("/api/scenarios/01/check", json={"replies": replies}).json()["checks"]
    assert checks == [{"item": "chamou buscar_produtos", "ok": True}]


def test_info_reports_store_clock_and_pipeline(client):
    info = client.get("/api/info").json()
    assert {"modelo", "agora", "loja_aberta", "status", "busca", "roteador"} <= info.keys()
