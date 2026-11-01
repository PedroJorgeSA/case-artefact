# Empório da Música — Agente de Atendimento

Protótipo da **Lia**, assistente virtual da Empório da Música (loja fictícia de instrumentos em Campo Grande/MS). Ela atende por mensagem de texto, consulta catálogo, promoções e pedidos em tempo real e aplica o manual de políticas da loja.

> 🚧 Em construção. Seções marcadas com TODO serão completadas ao longo do desenvolvimento.

## Como rodar

Pré-requisitos: [uv](https://docs.astral.sh/uv/) e uma chave da Claude API.

```bash
cp .env.example .env        # preencha ANTHROPIC_API_KEY
uv sync                     # cria o ambiente com Python 3.12 e instala dependências
uv run emporio              # abre o chat no terminal
uv run emporio --debug      # mostra as chamadas de ferramenta
uv run pytest               # testes das regras de negócio (não chamam o LLM)
```

Para continuar uma conversa anterior: `uv run emporio --session <id>`.

## Arquitetura

```
mensagem ─► Agent (loop de tool use) ─► Claude API
                 │                          │
                 │◄──── tool_use ───────────┘
                 ▼
           ToolExecutor ─► Store (pandas sobre os CSVs + regras de negócio)
```

| Arquivo | Papel |
|---|---|
| `src/emporio_agent/agent.py` | Loop do agente, persistência da sessão em `.sessions/` |
| `src/emporio_agent/tools.py` | Definição das 5 ferramentas e despacho |
| `src/emporio_agent/data.py` | Leitura dos CSVs; preço, PIX, parcelamento, prazos de troca e horário calculados em código |
| `src/emporio_agent/prompts.py` | Persona, regras de uso das ferramentas e manual de políticas |
| `knowledge/politicas.md` | Manual transcrito do PDF, com notas de curadoria |

## Decisões técnicas

TODO: detalhar. Resumo das escolhas até aqui:

- **Híbrido: function calling para dados + manual inteiro no prompt.** Os dados são pequenos (64 produtos, 20 pedidos) e estruturados, então ferramentas tipadas são mais precisas que busca vetorial. O manual tem ~3 mil tokens: cabe no prompt e fica em cache, sem a complexidade nem o risco de recuperação incompleta de um RAG.
- **Ferramentas estreitas em vez de text-to-SQL.** O modelo nunca vê a tabela de clientes; `consultar_pedido` só libera dados com número do pedido + e-mail ou telefone (LGPD).
- **Contas em código, não no LLM.** Desconto PIX (não cumulativo com promoção), parcelamento mínimo por faixa e prazos de devolução são calculados em `data.py` e testados.
- **Modelo:** `claude-opus-5-5` com esforço `medium`, configurável no `.env`. Fallback automático da API habilitado para recusas de classificadores de segurança.
- **Loop manual** em vez do tool runner (beta) do SDK, para controlar limite de iterações, logs e persistência.

## Premissas sobre os dados

TODO: completar. Já identificadas:

- Data de referência configurável (`EMPORIO_NOW`), porque os dados vão só até mar/2026.
- Sem data real de entrega: a previsão de entrega é usada para contar o prazo de arrependimento.
- Contradições do manual (telefone, e-mail, parcelamento) resolvidas em `knowledge/politicas.md`.

## Limitações conhecidas e próximos passos

TODO

## Uso de assistentes de código

TODO

## Exemplos de conversa

Ver pasta [`conversas/`](conversas/). TODO
