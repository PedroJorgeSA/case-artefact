"""Interface de linha de comando: um chat no terminal com a Lia."""

import argparse
import json

import groq

from .agent import Agent, Reply, ToolCall, Waiting

GREETING = "Lia: Oi! Eu sou a Lia, da Empório da Música 🎸 Como posso te ajudar hoje?"


def main() -> None:
    parser = argparse.ArgumentParser(description="Chat com a assistente da Empório da Música.")
    parser.add_argument("--session", help="ID de uma sessão anterior para continuar a conversa.")
    parser.add_argument("--debug", action="store_true", help="Mostra as chamadas de ferramenta.")
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
                if isinstance(event, Waiting):
                    print(f"  … aguardando o limite do Groq ({event.seconds:.0f}s)")
                elif isinstance(event, ToolCall) and args.debug:
                    status = "ERRO" if event.is_error else "ok"
                    print(f"  ⚙ {event.name}({json.dumps(event.args, ensure_ascii=False)}) -> {status}")
                elif isinstance(event, Reply):
                    reply = event
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

        print(f"\nLia: {reply.text}")

    print(f"\n[conversa salva; para continuar: uv run emporio --session {agent.session_id}]")


if __name__ == "__main__":
    main()
