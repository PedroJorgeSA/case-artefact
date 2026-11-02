"""Interface web: API com eventos em tempo real (SSE) e a página do chat.

Cada mensagem é respondida como um stream de eventos (`text/event-stream`), os
mesmos que o grafo emite: nó iniciado/terminado, intenção, trechos do manual,
chamadas ao modelo (tokens e tempo), ferramentas, esperas pelo limite do Groq,
verificação e a resposta final. Assim a página mostra o que a Lia está fazendo
enquanto ela trabalha. Os eventos de cada turno ficam salvos no checkpoint, e a
página redesenha a conversa inteira ao recarregar.
"""

import json
import os
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Literal

import groq
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config
from .agent import SESSION_ID_PATTERN, Agent
from .data import store_status
from .intent import load_classifier
from .prompts import WEEKDAYS_PT
from .rag import load_index
from .scenarios import SCENARIOS, evaluate

STATIC_DIR = Path(__file__).parent / "static"
SESSION_ID = f"^{SESSION_ID_PATTERN.pattern}$"


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    session_id: str | None = Field(default=None, pattern=SESSION_ID)


class FeedbackRequest(BaseModel):
    session_id: str = Field(pattern=SESSION_ID)
    turn: int = Field(ge=0)
    rating: Literal["up", "down"]
    reason: str | None = Field(default=None, max_length=200)


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _error_message(exc: Exception) -> str:
    if isinstance(exc, groq.AuthenticationError):
        return "A chave do Groq foi recusada. Confira GROQ_API_KEY no arquivo .env."
    if isinstance(exc, groq.RateLimitError):
        return "O limite gratuito do Groq continua esgotado. Tente de novo em um minuto."
    if isinstance(exc, groq.APIConnectionError):
        return "Sem conexão com a API do Groq."
    if isinstance(exc, groq.APIStatusError) and exc.status_code == 413:
        return "A conversa ficou grande demais para o limite gratuito do Groq. Comece uma nova conversa."
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
            "busca": load_index().mode,
            "roteador": "ml" if load_classifier() else "palavras-chave",
        }

    @app.get("/api/manual")
    def manual():
        index = load_index()
        return {"modo": index.mode, "secoes": [{"id": c.id, "secao": c.path, "texto": c.body} for c in index.chunks]}

    @app.get("/api/scenarios")
    def scenarios():
        return [{k: s[k] for k in ("id", "titulo", "mostra", "mensagens")} for s in SCENARIOS]

    @app.post("/api/scenarios/{scenario_id}/check")
    def check_scenario(scenario_id: str, body: dict):
        scenario = next((s for s in SCENARIOS if s["id"] == scenario_id), None)
        if scenario is None:
            raise HTTPException(404, "Cenário não encontrado.")
        return {"checks": evaluate(scenario, body.get("replies", []))}

    @app.get("/api/sessions/{session_id}")
    def history(session_id: str):
        if not SESSION_ID_PATTERN.fullmatch(session_id):
            raise HTTPException(404, "Conversa não encontrada.")
        agent = get_agent(session_id)
        if not agent.exists:
            raise HTTPException(404, "Conversa não encontrada.")
        return {"session_id": session_id, "turns": [turn["events"] for turn in agent.log]}

    @app.post("/api/feedback")
    def feedback(request: FeedbackRequest):
        config.FEEDBACK_DIR.mkdir(exist_ok=True)
        record = {**request.model_dump(), "em": datetime.now(config.STORE_TZ).isoformat(timespec="seconds")}
        with open(config.FEEDBACK_DIR / "avaliacoes.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return {"ok": True}

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
                    yield _sse(event)
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
    print("Carregando o índice do manual e o classificador de intenção…")
    load_index()
    load_classifier()
    print(f"Empório da Música: abra http://127.0.0.1:{port}")
    uvicorn.run(create_app(), host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
