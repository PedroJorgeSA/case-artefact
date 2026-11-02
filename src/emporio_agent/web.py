"""Interface web: API com eventos em tempo real (SSE) e a página do chat.

Cada mensagem é respondida como um stream de eventos (`text/event-stream`):
sessão, ferramentas chamadas, esperas pelo limite do Groq e a resposta final.
Assim a página mostra o que a Lia está fazendo enquanto ela trabalha.
"""

import json
import os
import threading
from collections.abc import Callable
from pathlib import Path

import groq
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config
from .agent import SESSION_ID_PATTERN, Agent, Reply, ToolCall, Waiting
from .data import store_status
from .prompts import WEEKDAYS_PT

STATIC_DIR = Path(__file__).parent / "static"


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    session_id: str | None = Field(default=None, pattern=f"^{SESSION_ID_PATTERN.pattern}$")


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _tool_payload(name: str, args: dict, result: str) -> dict:
    parsed = json.loads(result)
    return {"type": "tool", "name": name, "args": args, "result": parsed, "is_error": "erro" in parsed}


def _event_payload(event) -> dict:
    if isinstance(event, ToolCall):
        return _tool_payload(event.name, event.args, event.result)
    if isinstance(event, Waiting):
        return {"type": "waiting", "seconds": event.seconds}
    if isinstance(event, Reply):
        return {"type": "reply", "text": event.text}
    raise TypeError(f"Evento desconhecido: {event!r}")


def _history_items(messages: list[dict]) -> list[dict]:
    """Converte o histórico salvo na mesma forma dos eventos, para a página redesenhar a conversa."""
    items, pending = [], {}
    for message in messages:
        if message["role"] == "user":
            items.append({"type": "user", "text": message["content"]})
        elif message["role"] == "assistant" and message.get("tool_calls"):
            for call in message["tool_calls"]:
                pending[call["id"]] = call["function"]
        elif message["role"] == "assistant":
            items.append({"type": "reply", "text": message["content"]})
        elif message["role"] == "tool" and message["tool_call_id"] in pending:
            function = pending.pop(message["tool_call_id"])
            try:
                args = json.loads(function["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
            items.append(_tool_payload(function["name"], args, message["content"]))
    return items


def _error_message(exc: Exception) -> str:
    if isinstance(exc, groq.AuthenticationError):
        return "A chave do Groq foi recusada. Confira GROQ_API_KEY no arquivo .env."
    if isinstance(exc, groq.RateLimitError):
        return "O limite gratuito do Groq continua esgotado. Tente de novo em um minuto."
    if isinstance(exc, groq.APIConnectionError):
        return "Sem conexão com a API do Groq."
    if isinstance(exc, groq.APIStatusError):
        return f"A API do Groq respondeu com erro {exc.status_code}."
    return "Erro inesperado no servidor. Veja o terminal para detalhes."


def create_app(agent_factory: Callable[..., Agent] = Agent) -> FastAPI:
    app = FastAPI(title="Empório da Música · atendimento")
    agents: dict[str, Agent] = {}
    busy: dict[str, threading.Lock] = {}
    registry = threading.Lock()

    def get_agent(session_id: str | None) -> Agent:
        with registry:
            if session_id in agents:
                return agents[session_id]
            agent = agent_factory(session_id=session_id)
            agents[agent.session_id] = agent
            busy[agent.session_id] = threading.Lock()
            return agent

    @app.get("/api/info")
    def info():
        now = config.now()
        status = store_status(now)
        return {
            "modelo": config.MODEL,
            "agora": f"{WEEKDAYS_PT[now.weekday()][:3]}, {now:%d/%m/%Y %H:%M}",
            "loja_aberta": status["aberta_agora"],
            "status": f"Aberta até {status['fecha_as']}" if status["aberta_agora"] else f"Fechada, reabre {status.get('reabre', 'no próximo dia útil')}",
        }

    @app.get("/api/sessions/{session_id}")
    def history(session_id: str):
        if not SESSION_ID_PATTERN.fullmatch(session_id) or not (config.SESSIONS_DIR / f"{session_id}.json").exists():
            raise HTTPException(404, "Conversa não encontrada.")
        return {"session_id": session_id, "items": _history_items(get_agent(session_id).messages)}

    @app.post("/api/chat")
    def chat(request: ChatRequest):
        agent = get_agent(request.session_id)
        lock = busy[agent.session_id]
        if not lock.acquire(blocking=False):
            raise HTTPException(409, "Esta conversa ainda está respondendo a mensagem anterior.")

        def events():
            try:
                yield _sse({"type": "session", "session_id": agent.session_id})
                for event in agent.stream(request.message):
                    yield _sse(_event_payload(event))
            except Exception as exc:  # noqa: BLE001 — o erro vira um evento para a página
                yield _sse({"type": "error", "message": _error_message(exc)})
                if not isinstance(exc, groq.APIError):
                    raise
            finally:
                lock.release()

        return StreamingResponse(
            events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        )

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    return app


def main() -> None:
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    print(f"Empório da Música: abra http://127.0.0.1:{port}")
    uvicorn.run(create_app(), host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
