"""Loop do agente: API do Groq + function calling, com histórico persistido em JSON.

Loop manual para ter controle explícito de limite de iterações, esperas por
limite de taxa, log das chamadas de ferramenta e persistência da sessão.

`stream()` devolve eventos à medida que acontecem (ferramenta chamada, espera
pelo Groq, resposta final), o que permite à interface web mostrar o progresso.
`send()` é o atalho que só devolve a resposta final.
"""

import json
import re
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field

import groq

from . import config
from .prompts import build_system
from .tools import TOOLS, ToolExecutor

MAX_ATTEMPTS = 6
# O ID vira nome de arquivo em .sessions/, então só aceita caracteres seguros.
SESSION_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}")
ESCALATION_TEXT = (
    "Essa ficou mais complicada do que eu esperava. Vou pedir para alguém da equipe te responder, tudo bem?"
)


@dataclass
class ToolCall:
    name: str
    args: dict
    result: str
    is_error: bool


@dataclass
class Waiting:
    """O Groq pediu para esperar: limite de tokens por minuto do plano gratuito."""

    seconds: float


@dataclass
class Reply:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)


Event = ToolCall | Waiting | Reply


def _retry_after(exc: groq.APIStatusError) -> float:
    try:
        seconds = float(exc.response.headers.get("retry-after", 10))
    except (TypeError, ValueError):
        seconds = 10.0
    return min(max(seconds, 1.0), 60.0)


class Agent:
    def __init__(self, session_id: str | None = None, client: groq.Groq | None = None):
        # Sem retentativas no SDK: as esperas por limite de taxa são feitas aqui,
        # para poderem ser avisadas ao usuário.
        self.client = client or groq.Groq(api_key=config.API_KEY, max_retries=0)
        if session_id is not None and not SESSION_ID_PATTERN.fullmatch(session_id):
            raise ValueError(f"ID de sessão inválido: {session_id!r}")
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

    def _call_model(self) -> Iterator[Waiting]:
        """Chama o modelo. Gerador: avisa cada espera e, no fim, retorna a resposta."""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                return self.client.chat.completions.create(
                    model=config.MODEL,
                    messages=[self.system, *self.messages],
                    tools=TOOLS,
                    tool_choice="auto",
                    reasoning_effort=config.EFFORT,
                    temperature=0.3,
                    max_completion_tokens=4096,
                )
            except groq.RateLimitError as exc:
                if attempt == MAX_ATTEMPTS:
                    raise
                wait = _retry_after(exc)
            except (groq.InternalServerError, groq.APIConnectionError):
                if attempt == MAX_ATTEMPTS:
                    raise
                wait = 2.0 * attempt
            except groq.BadRequestError as exc:
                # O gpt-oss às vezes gera uma chamada de ferramenta malformada
                # (erro tool_use_failed); repetir a chamada costuma resolver.
                if "tool_use_failed" not in str(exc) or attempt == MAX_ATTEMPTS:
                    raise
                wait = 0.0
            if wait:
                yield Waiting(wait)
                time.sleep(wait)
        raise RuntimeError("unreachable")

    def stream(self, user_text: str) -> Iterator[Event]:
        """Processa uma mensagem do cliente, devolvendo eventos até a resposta final."""
        turn_start = len(self.messages)
        self.messages.append({"role": "user", "content": user_text})
        tool_calls: list[ToolCall] = []
        try:
            for _ in range(config.MAX_TOOL_ITERATIONS):
                response = yield from self._call_model()
                message = response.choices[0].message
                # Só os campos que a API aceita de volta (o raciocínio interno fica de fora).
                assistant = {"role": "assistant", "content": message.content or ""}
                if message.tool_calls:
                    assistant["tool_calls"] = [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {"name": c.function.name, "arguments": c.function.arguments},
                        }
                        for c in message.tool_calls
                    ]
                self.messages.append(assistant)

                if not message.tool_calls:
                    text = (message.content or "").strip()
                    break

                for call in message.tool_calls:
                    try:
                        args = json.loads(call.function.arguments or "{}")
                        output, is_error = self.executor.run(call.function.name, args)
                    except json.JSONDecodeError:
                        args = {}
                        output, is_error = json.dumps({"erro": "Argumentos em JSON inválido."}), True
                    tool_call = ToolCall(call.function.name, args, output, is_error)
                    tool_calls.append(tool_call)
                    self.messages.append({"role": "tool", "tool_call_id": call.id, "content": output})
                    yield tool_call
            else:
                text = ESCALATION_TEXT
        except BaseException:
            # Turno incompleto não fica no histórico, para a próxima tentativa
            # começar de um estado consistente.
            del self.messages[turn_start:]
            raise

        self._save()
        yield Reply(text, tool_calls)

    def send(self, user_text: str) -> Reply:
        reply = None
        for event in self.stream(user_text):
            if isinstance(event, Reply):
                reply = event
        return reply
