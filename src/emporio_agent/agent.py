"""Loop do agente: API do Groq + function calling, com histórico persistido em JSON.

Loop manual para ter controle explícito de limite de iterações, log das chamadas
de ferramenta e persistência da sessão.
"""

import json
import uuid
from dataclasses import dataclass, field

import groq

from . import config
from .prompts import build_system
from .tools import TOOLS, ToolExecutor


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
    def __init__(self, session_id: str | None = None, client: groq.Groq | None = None):
        self.client = client or groq.Groq(api_key=config.API_KEY, max_retries=5)
        self.executor = ToolExecutor()
        self.session_id = session_id or uuid.uuid4().hex[:8]
        self.messages: list[dict] = []
        # Data/hora fixada no início da sessão: o system prompt fica idêntico entre
        # mensagens, o que permite ao Groq reaproveitar o prefixo em cache.
        self.system = {"role": "system", "content": build_system(config.now())}
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
        return self.client.chat.completions.create(
            model=config.MODEL,
            messages=[self.system, *self.messages],
            tools=TOOLS,
            tool_choice="auto",
            reasoning_effort=config.EFFORT,
            temperature=0.3,
            max_completion_tokens=4096,
        )

    def send(self, user_text: str) -> Reply:
        self.messages.append({"role": "user", "content": user_text})
        reply = Reply(text="")

        for _ in range(config.MAX_TOOL_ITERATIONS):
            message = self._call_model().choices[0].message
            # Só os campos que a API aceita de volta (o raciocínio interno fica de fora).
            assistant = {"role": "assistant", "content": message.content or ""}
            if message.tool_calls:
                assistant["tool_calls"] = [
                    {"id": c.id, "type": "function", "function": {"name": c.function.name, "arguments": c.function.arguments}}
                    for c in message.tool_calls
                ]
            self.messages.append(assistant)

            if not message.tool_calls:
                reply.text = (message.content or "").strip()
                break

            for call in message.tool_calls:
                try:
                    args = json.loads(call.function.arguments or "{}")
                    output, is_error = self.executor.run(call.function.name, args)
                except json.JSONDecodeError:
                    args = {}
                    output, is_error = json.dumps({"erro": "Argumentos em JSON inválido."}), True
                reply.tool_calls.append(ToolCall(call.function.name, args, output, is_error))
                self.messages.append({"role": "tool", "tool_call_id": call.id, "content": output})
        else:
            reply.text = (
                "Opa, essa ficou mais complicada do que eu esperava. Vou pedir para alguém da equipe te "
                "responder, tudo bem?"
            )

        self._save()
        return reply
