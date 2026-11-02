"""Roda os cenários de exemplo contra o modelo e salva cada conversa em conversas/.

Uso:
    uv run python scripts/gerar_conversas.py            # todos os cenários
    uv run python scripts/gerar_conversas.py 03 05      # só alguns
    uv run python scripts/gerar_conversas.py 05 --continuar   # retoma de onde parou

Cada cenário usa uma sessão com ID fixo (ex.: exemplo-03), então dá para abrir a
mesma conversa na interface web: http://127.0.0.1:8000/?sessao=exemplo-03
Os cenários e o resultado esperado de cada um ficam em src/emporio_agent/scenarios.py.
"""

import json
import sqlite3
import sys
from pathlib import Path

from emporio_agent import config
from emporio_agent.agent import Agent
from emporio_agent.prompts import WEEKDAYS_PT
from emporio_agent.scenarios import SCENARIOS, evaluate

OUT_DIR = Path(__file__).resolve().parents[1] / "conversas"


def _args(args: dict) -> str:
    return ", ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in args.items() if v is not None and v != "" and v is not False)


def _forget(session_id: str) -> None:
    """Apaga a sessão do checkpoint, para o cenário começar do zero."""
    path = config.SESSIONS_DIR / "conversas.sqlite"
    if path.exists():
        with sqlite3.connect(path) as db:
            for table in ("checkpoints", "writes"):
                db.execute(f"DELETE FROM {table} WHERE thread_id = ?", (session_id,))


def _turn_lines(events: list[dict]) -> tuple[list[str], dict | None]:
    """Markdown de um turno a partir dos eventos salvos: mensagem, caminho no grafo e resposta."""
    lines, trace, reply = [], [], None
    for event in events:
        kind = event["type"]
        if kind == "user":
            lines += [f"**Cliente:** {event['text']}", ""]
        elif kind == "guard" and event["sinais"]:
            trace.append(f"guardrail: {', '.join(event['sinais'])}")
        elif kind == "route":
            trace.append(f"intenção: {', '.join(event['intents'])}")
        elif kind == "retrieval":
            ids = ", ".join(f"§{t['id']}" for t in event["trechos"]) or "nenhum trecho"
            trace.append(f"manual ({event['source']}): {ids}")
        elif kind == "tool":
            status = "erro" if event["is_error"] else "ok"
            trace.append(f"`{event['name']}({_args(event['args'])})` → {status}")
        elif kind == "check" and event["status"] != "aprovada":
            trace.append(f"verificação: {event['status']}" + (f" ({'; '.join(event['issues'])})" if event["issues"] else ""))
        elif kind == "reply":
            reply = event
            lines += ([f"> {item}" for item in trace] + [""]) if trace else []
            lines += [f"**Lia:** {event['text']}", ""]
            if event["options"]:
                lines += ["*Opções de resposta rápida:* " + " · ".join(event["options"]), ""]
            lines += [f"<sub>[§{c['id']}] {c['secao']}</sub><br>" for c in event["citations"]]
            if event["citations"]:
                lines.append("")
    return lines, reply


def run(scenario: dict, resume: bool = False) -> str:
    """Roda o cenário e monta o markdown a partir do log salvo da conversa.

    Com resume=True, turnos já concluídos (salvos no checkpoint) não são refeitos:
    útil quando o limite diário do Groq interrompe a conversa no meio.
    """
    session_id = f"exemplo-{scenario['id']}"
    if not resume:
        _forget(session_id)
    agent = Agent(session_id=session_id)

    for text in scenario["mensagens"][len(agent.log):]:
        print(f"  cliente: {text}")
        for event in agent.stream(text):
            if event["type"] == "waiting":
                print(f"    (aguardando {event['seconds']:.0f}s)")
            elif event["type"] == "tool":
                print(f"    ferramenta: {event['name']} -> {'erro' if event['is_error'] else 'ok'}")

    now = config.now()
    lines = [
        f"# {scenario['id']} · {scenario['titulo']}",
        "",
        f"**O que mostra:** {scenario['mostra']}",
        "",
        f"Modelo `{config.MODEL}` · busca no manual: {agent.index.mode} · data simulada "
        f"{WEEKDAYS_PT[now.weekday()]}, {now:%d/%m/%Y %H:%M} · gerado por `scripts/gerar_conversas.py` · "
        f"na interface: `/?sessao={session_id}`",
        "",
        "Linhas com `>` mostram o que aconteceu no grafo entre a mensagem e a resposta.",
        "",
        "---",
        "",
    ]
    replies = []
    for turn in agent.log:
        turn_lines, reply = _turn_lines(turn["events"])
        lines += turn_lines
        if reply:
            replies.append(reply)

    checks = evaluate(scenario, replies)
    if checks:
        lines += ["---", "", "**Resultado esperado:**", ""]
        lines += [f"- [{'x' if c['ok'] else ' '}] {c['item']}" for c in checks]
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    sys.stdout.reconfigure(line_buffering=True)  # progresso visível mesmo redirecionado para arquivo
    args = sys.argv[1:]
    resume = "--continuar" in args
    chosen = {a for a in args if a != "--continuar"}
    OUT_DIR.mkdir(exist_ok=True)
    for scenario in SCENARIOS:
        if chosen and scenario["id"] not in chosen:
            continue
        print(f"[{scenario['id']}] {scenario['titulo']}")
        path = OUT_DIR / f"{scenario['id']}-{scenario['slug']}.md"
        path.write_text(run(scenario, resume), encoding="utf-8")
        print(f"  salvo em {path.relative_to(OUT_DIR.parent)}")


if __name__ == "__main__":
    main()
