"""Interface de linha de comando: um chat no terminal com a Lia."""

import argparse
import json

import anthropic

from .agent import Agent

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
            reply = agent.send(user_text)
        except anthropic.AuthenticationError:
            print("\n[erro] Credencial inválida. Confira ANTHROPIC_API_KEY no arquivo .env.")
            break
        except anthropic.RateLimitError:
            print("\n[erro] Limite de requisições atingido. Tente de novo em instantes.")
            continue
        except anthropic.APIStatusError as e:
            print(f"\n[erro] API retornou {e.status_code}: {e.message}")
            continue
        except anthropic.APIConnectionError:
            print("\n[erro] Sem conexão com a API.")
            continue

        if args.debug:
            for call in reply.tool_calls:
                status = "ERRO" if call.is_error else "ok"
                print(f"  ⚙ {call.name}({json.dumps(call.args, ensure_ascii=False)}) -> {status}")
        print(f"\nLia: {reply.text}")

    print(f"\n[conversa salva; para continuar: uv run emporio --session {agent.session_id}]")


if __name__ == "__main__":
    main()
