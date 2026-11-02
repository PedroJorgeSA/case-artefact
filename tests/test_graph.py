"""Testes do grafo do agente com um cliente Groq falso (sem chamar a API)."""

import json
from types import SimpleNamespace

import groq
import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver

from emporio_agent import graph as graph_module
from emporio_agent.agent import Agent
from emporio_agent.graph import OLD_TOOL_RESULT_CHARS, context_window
from emporio_agent.rag import load_index


def _message(content="", tool_calls=None):
    usage = SimpleNamespace(prompt_tokens=1000, completion_tokens=50)
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls))], usage=usage)


def _tool_call(call_id, name, args):
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=json.dumps(args)))


def _rate_limit():
    response = httpx.Response(429, headers={"retry-after": "3"}, request=httpx.Request("POST", "https://groq.test"))
    return groq.RateLimitError("rate limit", response=response, body=None)


class FakeClient:
    """Devolve (ou levanta) os itens da fila, um por chamada, e guarda o que recebeu."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(graph_module.time, "sleep", lambda _: None)


def make_agent(*outcomes, checkpointer=None, session_id=None):
    client = FakeClient(*outcomes)
    agent = Agent(session_id=session_id, client=client, checkpointer=checkpointer or InMemorySaver(), index=load_index(False))
    return agent, client


def _types(events):
    return [e["type"] for e in events if e["type"] != "step"]


def test_tool_call_then_answer():
    agent, _ = make_agent(
        _message(tool_calls=[_tool_call("c1", "detalhes_produto", {"produto_id": 90})]),
        _message("Está em promoção sim."),
    )
    events = list(agent.stream("O produto 90 está em promoção?"))
    tool = next(e for e in events if e["type"] == "tool")
    assert tool["name"] == "detalhes_produto" and "R$ 1.967,18" in json.dumps(tool["result"], ensure_ascii=False)
    assert events[-2]["type"] == "reply" and events[-2]["text"] == "Está em promoção sim."
    assert all("t" in e for e in events) and events[-1]["t"] >= events[0]["t"]
    assert [m["role"] for m in agent.messages] == ["user", "assistant", "tool", "assistant"]
    assert len(agent.log) == 1


def test_graph_path_is_reported_step_by_step():
    agent, _ = make_agent(_message("Oi! Como posso ajudar?"))
    events = list(agent.stream("oi"))
    started = [e["node"] for e in events if e["type"] == "step" and e["status"] == "start"]
    assert started == ["guard", "route", "agent", "verify", "finalize"]  # saudação não passa pelo manual


def test_policy_question_retrieves_manual_before_the_model():
    agent, client = make_agent(_message("Pode devolver em até 7 dias [§4.1]."))
    events = list(agent.stream("Me arrependi da compra, posso devolver?"))
    retrieval = next(e for e in events if e["type"] == "retrieval")
    assert retrieval["trechos"][0]["id"] == "4.1"
    sent = client.requests[0]["messages"]
    notes = next(m for m in sent[1:] if m["role"] == "system")
    assert "[§4.1]" in notes["content"]
    assert sent.index(notes) == len(sent) - 2  # trechos logo antes da mensagem atual
    reply = next(e for e in events if e["type"] == "reply")
    assert [c["id"] for c in reply["citations"]] == ["4.1"]


def test_invented_price_is_sent_back_once_then_corrected():
    agent, client = make_agent(
        _message(tool_calls=[_tool_call("c1", "detalhes_produto", {"produto_id": 81})]),
        _message("O C40 sai por R$ 499,00."),
        _message("O C40 sai por **R$ 599,90**."),
    )
    events = list(agent.stream("quanto custa o yamaha c40?"))
    checks = [e for e in events if e["type"] == "check"]
    assert [c["status"] for c in checks] == ["refazer", "corrigida"]
    assert "R$ 499,00" in client.requests[2]["messages"][-1]["content"]
    assert agent.messages[-1]["content"] == "O C40 sai por **R$ 599,90**."  # a versão recusada não fica no histórico


def test_check_gives_up_after_one_revision():
    agent, _ = make_agent(_message("Custa R$ 12,00."), _message("Custa R$ 13,00."))
    events = list(agent.stream("oi"))
    assert [e["status"] for e in events if e["type"] == "check"] == ["refazer", "alerta"]
    assert next(e for e in events if e["type"] == "reply")["check"]["status"] == "alerta"


def test_quick_reply_options_are_parsed_out_of_the_text():
    agent, _ = make_agent(_message("Prefere nylon ou aço?\n[[opções: Nylon | Aço]]"))
    reply = agent.send("quero um violão")
    assert reply["text"] == "Prefere nylon ou aço?"
    assert reply["options"] == ["Nylon", "Aço"]


def test_card_number_never_reaches_the_model():
    agent, client = make_agent(_message("Não precisa enviar dados de cartão por aqui."))
    events = list(agent.stream("meu cartão é 4111 1111 1111 1111"))
    assert "cartao_removido" in next(e for e in events if e["type"] == "guard")["sinais"]
    sent = json.dumps(client.requests[0]["messages"], ensure_ascii=False)
    assert "4111" not in sent and "nunca pede esses dados" in sent
    assert "4111" not in json.dumps(agent.messages)


def test_contacts_are_masked_in_tool_events():
    agent, _ = make_agent(
        _message(tool_calls=[_tool_call("c1", "consultar_pedido", {"numero_pedido": 7, "email_ou_telefone": "leticia.rocha@jmail.com"})]),
        _message("Achei seu pedido."),
    )
    tool = next(e for e in agent.stream("pedido 7, leticia.rocha@jmail.com") if e["type"] == "tool")
    assert tool["args"]["email_ou_telefone"] == "l***@jmail.com"
    assert "leticia.rocha" not in json.dumps(tool["result"])


def test_rate_limit_is_announced_and_retried():
    agent, _ = make_agent(_rate_limit(), _message("Oi!"))
    events = list(agent.stream("oi"))
    assert any(e["type"] == "waiting" and e["seconds"] == 3.0 for e in events)
    assert agent.messages[-1]["content"] == "Oi!"


def test_malformed_generation_is_retried():
    response = httpx.Response(400, request=httpx.Request("POST", "https://groq.test"))
    error = groq.BadRequestError("Error code: 400 - output_parse_failed", response=response, body=None)
    agent, _ = make_agent(error, _message("Oi!"))
    assert agent.send("oi")["text"] == "Oi!"


def test_failed_turn_is_not_kept_in_history():
    error = groq.APIStatusError("erro", response=httpx.Response(401, request=httpx.Request("POST", "https://groq.test")), body=None)
    agent, _ = make_agent(_message("Primeira resposta."), error)
    agent.send("oi")
    with pytest.raises(groq.APIStatusError):
        agent.send("de novo")
    assert [m["content"] for m in agent.messages] == ["oi", "Primeira resposta."]


def test_invalid_tool_arguments_go_back_as_error():
    bad_call = SimpleNamespace(id="c1", function=SimpleNamespace(name="detalhes_produto", arguments="{quebrado"))
    agent, _ = make_agent(_message(tool_calls=[bad_call]), _message("Pode repetir?"))
    events = list(agent.stream("detalhes"))
    assert next(e for e in events if e["type"] == "tool")["is_error"]


def test_old_tool_results_are_shortened_only_in_context():
    long_result = "x" * 2000
    messages = [
        {"role": "user", "content": "primeira"},
        {"role": "assistant", "content": "", "tool_calls": []},
        {"role": "tool", "tool_call_id": "c1", "content": long_result},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "segunda"},
        {"role": "tool", "tool_call_id": "c2", "content": long_result},
    ]
    window = context_window(messages, fixed_tokens=1000)
    assert len(window[2]["content"]) < OLD_TOOL_RESULT_CHARS + 100  # turno anterior: encurtado
    assert window[5]["content"] == long_result  # turno atual: completo
    assert messages[2]["content"] == long_result  # histórico intacto


def test_context_window_drops_old_turns_over_budget():
    messages = []
    for i in range(10):
        messages += [{"role": "user", "content": f"pergunta {i} " + "y" * 3000}, {"role": "assistant", "content": "ok"}]
    window = context_window(messages, fixed_tokens=2000)
    assert window[0]["content"].startswith("pergunta 9") or len(window) < len(messages)
    assert window[-1] == messages[-1]


def test_conversation_survives_a_new_agent_with_the_same_checkpointer():
    saver = InMemorySaver()
    first, _ = make_agent(_message("Oi!"), checkpointer=saver, session_id="sessao-1")
    first.send("oi")
    second, _ = make_agent(checkpointer=saver, session_id="sessao-1")
    assert [m["content"] for m in second.messages] == ["oi", "Oi!"]
    assert second.exists


def test_values_the_customer_gave_as_filters_are_not_flagged():
    agent, _ = make_agent(
        _message(tool_calls=[_tool_call("c1", "buscar_produtos", {"categoria": "Violões", "preco_max": 1000})]),
        _message("Separei opções até R$ 1.000."),
    )
    events = list(agent.stream("violão até uns mil reais"))
    assert [e["status"] for e in events if e["type"] == "check"] == ["aprovada"]
