"""Loop do agente: Claude API + function calling, com histórico persistido em JSON.

Loop manual (em vez do tool runner beta do SDK) para ter controle explícito de
limite de iterações, log das chamadas de ferramenta e persistência da sessão.
"""

import json
import uuid
from dataclasses import dataclass, field

import anthropic

from . import config
from .prompts import build_system
from .tools import TOOLS, ToolExecutor

FALLBACK_BETA = "server-side-fallback-2026-07-01"


@dataclass
class ToolCall:
    name: str
    args: dict
    result: str
    is_error: bool


@dataclass
class Reply:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)


class Agent:
    def __init__(self, session_id: str | None = None, client: anthropic.Anthropic | None = None):
        self.client = client or anthropic.Anthropic()
        self.executor = ToolExecutor()
        self.session_id = session_id or uuid.uuid4().hex[:8]
        self.messages: list[dict] = []
        # Data/hora fixada no início da sessão: o system prompt fica idêntico entre
        # mensagens e o histórico inteiro aproveita o cache.
        self.system = build_system(config.now())
        self._load()

    @property
    def _session_file(self):
        return config.SESSIONS_DIR / f"{self.session_id}.json"

    def _load(self) -> None:
        if self._session_file.exists():
            self.messages = json.loads(self._session_file.read_text(encoding="utf-8"))["messages"]

    def _save(self) -> None:
        config.SESSIONS_DIR.mkdir(exist_ok=True)
        self._session_file.write_text(
            json.dumps({"session_id": self.session_id, "messages": self.messages}, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )

    def _call_model(self):
        return self.client.beta.messages.create(
            model=config.MODEL,
            max_tokens=16000,
            system=self.system,
            tools=TOOLS,
            messages=self.messages,
            output_config={"effort": config.EFFORT},
            cache_control={"type": "ephemeral"},
            # Se um classificador de segurança recusar, a API refaz a chamada no
            # modelo de fallback recomendado em vez de devolver a recusa.
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )

    def send(self, user_text: str) -> Reply:
        self.messages.append({"role": "user", "content": user_text})
        reply = Reply(text="")

        for _ in range(config.MAX_TOOL_ITERATIONS):
            response = self._call_model()
            if response.stop_reason == "refusal":
                # Recusa sem fallback possível: não entra no histórico.
                reply.text = "Desculpe, não consigo ajudar com isso. Posso ajudar com algo da loja?"
                break

            # O histórico é só acrescentado (nunca editado) e guarda os blocos
            # completos, inclusive os de thinking, como a API exige.
            self.messages.append(
                {"role": "assistant", "content": [b.model_dump(exclude_none=True) for b in response.content]}
            )
            if response.stop_reason != "tool_use":
                reply.text = "\n".join(b.text for b in response.content if b.type == "text").strip()
                break

            results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                output, is_error = self.executor.run(block.name, block.input)
                reply.tool_calls.append(ToolCall(block.name, block.input, output, is_error))
                results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": output, "is_error": is_error}
                )
            # Todos os resultados de uma rodada vão juntos numa única mensagem.
            self.messages.append({"role": "user", "content": results})
        else:
            reply.text = (
                "Opa, essa ficou mais complicada do que eu esperava. Vou pedir para alguém da equipe te "
                "responder, tudo bem?"
            )

        self._save()
        return reply
