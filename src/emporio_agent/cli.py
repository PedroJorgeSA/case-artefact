"""Interface de linha de comando: um chat no terminal com a Lia."""

import argparse
import json

import groq

from .agent import Agent
from .guardrails import strip_citations

GREETING = "Lia: Oi! Eu sou a Lia, da Empório da Música 🎸 Como posso te ajudar hoje?"


def _print_debug(event: dict) -> None:
    kind = event["type"]
    if kind == "route":
        print(f"  ◇ intenção: {', '.join(event['intents'])} ({event['method']})" + (" → manual" if event["retrieval"] else ""))
    elif kind == "retrieval":
        ids = ", ".join(f"§{t['id']}" for t in event["trechos"]) or "nada"
        print(f"  ◇ manual ({event['modo']}, confiança {event['confianca']}): {ids}")
    elif kind == "tool":
        status = "ERRO" if event["is_error"] else "ok"
        print(f"  ⚙ {event['name']}({json.dumps(event['args'], ensure_ascii=False)}) -> {status}")
    elif kind == "llm":
        print(f"  ◇ modelo: {event['prompt_tokens']} tokens de entrada, {event['completion_tokens']} de saída, {event['ms']} ms")
    elif kind == "check":
        print(f"  ◇ verificação: {event['status']}" + "".join(f"\n      - {i}" for i in event["issues"]))
    elif kind == "guard" and event["sinais"]:
        print(f"  ◇ guardrail: {', '.join(event['sinais'])}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Chat com a assistente da Empório da Música.")
    parser.add_argument("--session", help="ID de uma sessão anterior para continuar a conversa.")
    parser.add_argument("--debug", action="store_true", help="Mostra o caminho no grafo: intenção, trechos do manual, ferramentas e verificação.")
    args = parser.parse_args()

    agent = Agent(session_id=args.session)
    print(f"[sessão {agent.session_id} — digite 'sair' para encerrar]\n")
    if not agent.messages:
        print(GREETING)

    while True:
        try:
            user_text = input("\nVocê: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not user_text:
            continue
        if user_text.lower() in {"sair", "exit", "quit"}:
            break

        try:
            reply = None
            for event in agent.stream(user_text):
                kind = event["type"]
                if kind == "waiting":
                    print(f"  … aguardando o limite do Groq ({event['seconds']:.0f}s)")
                elif kind == "reply":
                    reply = event
                elif args.debug:
                    _print_debug(event)
        except groq.AuthenticationError:
            print("\n[erro] Credencial inválida. Confira GROQ_API_KEY no arquivo .env.")
            break
        except groq.RateLimitError:
            print("\n[erro] Limite do Groq atingido mesmo após esperar. Tente de novo em um minuto.")
            continue
        except groq.APIStatusError as e:
            print(f"\n[erro] API retornou {e.status_code}: {e.message}")
            continue
        except groq.APIConnectionError:
            print("\n[erro] Sem conexão com a API.")
            continue

        text = reply["text"] if args.debug else strip_citations(reply["text"])
        print(f"\nLia: {text}")
        if reply["options"]:
            print("     [" + " | ".join(reply["options"]) + "]")

    print(f"\n[conversa salva; para continuar: uv run emporio --session {agent.session_id}]")


if __name__ == "__main__":
    main()
