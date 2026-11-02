"""Testes do loop do agente com um cliente Groq falso (sem chamar a API)."""

import json
from types import SimpleNamespace

import groq
import httpx
import pytest

from emporio_agent import agent as agent_module
from emporio_agent import config
from emporio_agent.agent import Agent, Reply, ToolCall, Waiting


def _message(content="", tool_calls=None):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls))])


def _tool_call(call_id, name, args):
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=json.dumps(args)))


def _rate_limit():
    response = httpx.Response(429, headers={"retry-after": "3"}, request=httpx.Request("POST", "https://groq.test"))
    return groq.RateLimitError("rate limit", response=response, body=None)


class FakeClient:
    """Devolve (ou levanta) os itens da fila, um por chamada."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **_):
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(agent_module.time, "sleep", lambda _: None)


def test_tool_call_then_answer():
    client = FakeClient(
        _message(tool_calls=[_tool_call("c1", "detalhes_produto", {"produto_id": 90})]),
        _message("Está em promoção sim."),
    )
    agent = Agent(client=client)
    events = list(agent.stream("O produto 90 está em promoção?"))

    assert isinstance(events[0], ToolCall) and events[0].name == "detalhes_produto"
    assert "R$ 1.967,18" in events[0].result
    assert isinstance(events[-1], Reply) and events[-1].text == "Está em promoção sim."
    assert [m["role"] for m in agent.messages] == ["user", "assistant", "tool", "assistant"]
    assert (config.SESSIONS_DIR / f"{agent.session_id}.json").exists()


def test_rate_limit_is_announced_and_retried():
    agent = Agent(client=FakeClient(_rate_limit(), _message("Oi!")))
    events = list(agent.stream("oi"))
    assert events[0] == Waiting(3.0)
    assert events[-1].text == "Oi!"


def test_failed_turn_is_not_kept_in_history():
    error = groq.APIStatusError(
        "erro", response=httpx.Response(401, request=httpx.Request("POST", "https://groq.test")), body=None
    )
    agent = Agent(client=FakeClient(error))
    with pytest.raises(groq.APIStatusError):
        agent.send("oi")
    assert agent.messages == []


def test_invalid_tool_arguments_go_back_as_error():
    bad_call = SimpleNamespace(id="c1", function=SimpleNamespace(name="detalhes_produto", arguments="{quebrado"))
    agent = Agent(client=FakeClient(_message(tool_calls=[bad_call]), _message("Pode repetir?")))
    reply = agent.send("detalhes")
    assert reply.tool_calls[0].is_error
    assert reply.text == "Pode repetir?"


def test_old_tool_results_are_shortened_only_in_context():
    agent = Agent(client=FakeClient())
    long_result = "x" * 2000
    agent.messages = [
        {"role": "user", "content": "primeira"},
        {"role": "assistant", "content": "", "tool_calls": []},
        {"role": "tool", "tool_call_id": "c1", "content": long_result},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "segunda"},
        {"role": "tool", "tool_call_id": "c2", "content": long_result},
    ]
    context = agent._context()
    assert len(context[2]["content"]) < 600  # turno anterior: encurtado
    assert context[5]["content"] == long_result  # turno atual: completo
    assert agent.messages[2]["content"] == long_result  # histórico salvo intacto


def test_malformed_generation_is_retried():
    response = httpx.Response(400, request=httpx.Request("POST", "https://groq.test"))
    error = groq.BadRequestError("Error code: 400 - output_parse_failed", response=response, body=None)
    agent = Agent(client=FakeClient(error, _message("Oi!")))
    assert agent.send("oi").text == "Oi!"
