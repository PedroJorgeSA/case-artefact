# Empório da Música · customer-service agent "Lia"

> 🇧🇷 Versão em português: [README.pt-BR.md](README.pt-BR.md)

**Lia** is a prototype customer-service agent for Empório da Música, a fictional musical-instrument store in Campo Grande (MS, Brazil). Customers chat with her in Portuguese, WhatsApp-style. She answers with the store's real data (product, promotion and order spreadsheets) and its policy manual.

![Conversation on the left, the graph trace of every message on the right](docs/img/desktop.png)

*Left: the conversation as the customer sees it. Right: what happened inside, node by node: detected intent, manual sections retrieved (with scores), tool calls, tokens per model call, and the answer check.*

What Lia does:

- searches the catalog and quotes price, stock and active promotions;
- gives the PIX price and the maximum interest-free installments, computed in code;
- looks up an order, but only after the customer proves it is theirs (order number + registered e-mail or phone);
- answers return, warranty, shipping, payment and opening-hours questions from the manual, **citing the exact section** (`[§4.1]`);
- opens a ticket for the human team, with a summary they can act on without rereading the chat.

## Quick start

You need [uv](https://docs.astral.sh/uv/) (`brew install uv` on macOS) and a free Groq API key from [console.groq.com/keys](https://console.groq.com/keys).

```bash
cp .env.example .env        # then paste your key into GROQ_API_KEY=
uv sync
uv run emporio-web          # open http://127.0.0.1:8000
```

The first start downloads a small multilingual embedding model (~220 MB, cached in `.cache/`). Set `EMPORIO_EMBEDDINGS=off` to skip it; the manual search then runs on BM25 only.

Other commands:

```bash
uv run emporio --debug                     # terminal chat, printing the graph path of every message
uv run pytest                              # 60 tests, offline (fake Groq client, BM25 only)
uv run python scripts/avaliar.py           # offline evaluation of the router and of the manual search
uv run python scripts/gerar_conversas.py   # re-runs the 5 example conversations against the real model
```

## Using the interface

1. **Write as a customer**, or click one of the sample questions.
2. **Watch the "Percurso" tab.** Each message gets a block with:
   - a timeline per graph node;
   - intent probabilities;
   - the retrieved manual sections, with BM25, cosine and RRF scores and the cited ones highlighted;
   - every tool call with its rendered result;
   - prompt and completion tokens against the 8k-per-request limit;
   - the verdict of the answer check.

   The mini graph at the top lights up the path the message took.
3. **Click a citation** such as `§4.1` in an answer. The "Manual" tab opens on that exact section.
4. **Run a scenario.** The "Cenários" tab replays five scripted conversations and checks their expected outcome: tools called, sections cited, data that must *not* appear.
5. **Rate an answer.** "Não ajudou" asks for a reason (wrong value, wrong rule…), and the feedback goes to `.feedback/`.

Reloading the page restores both columns. Every event is stored with the conversation, and `?sessao=exemplo-03` opens one of the examples.

| Citation opens the manual | Scenarios with pass/fail checks | Phone |
|---|---|---|
| ![](docs/img/manual.png) | ![](docs/img/cenarios.png) | ![](docs/img/celular.png) |

## Architecture

```
            ┌──────────── LangGraph StateGraph, checkpointed in SQLite ─────────────┐
 message ─► │ guard ─► route ─┬─► retrieve ─┐                                         │
            │                 └─────────────┴─► agent ◄──► tools                     │
            │                                     │                                  │
            │                                     ▼                                  │
            │                                   verify ──(one retry)──► agent        │
            │                                     │                                  │
            │                                     ▼                                  │
            │                                  finalize ─► answer + citations + options
            └─────────────────────────────────────────────────────────────────────────┘
```

| Node | What it does | How |
|---|---|---|
| `guard` | Flags prompt-injection attempts. Strips card numbers (Luhn) and CPFs (check digits) **before** they reach the model or the history. | Regex + checksums, no LLM |
| `route` | Decides whether the message needs the policy manual. | **Traditional ML**: TF-IDF (char 2–5-grams + words) → one-vs-rest logistic regression |
| `retrieve` | Fetches the 3 most relevant manual sections. Short follow-ups ("e no PIX?") inherit the previous message as the query. | Hybrid RAG: BM25 + multilingual embeddings, fused with RRF |
| `agent` | Calls the LLM with the tools. Waits out Groq rate limits and reports the wait to the UI. | Groq SDK, `openai/gpt-oss-120b`, function calling |
| `tools` | Catalog search, product details, categories, order lookup (LGPD-gated), human handoff, and `consultar_manual` (RAG on demand when the router missed). | pandas over the CSVs; all math in Python |
| `verify` | Every R$ value, ticket number and `[§x]` citation in the draft must appear in this turn's sources. If not, the draft is sent back once with the problem. | Deterministic, no extra LLM call |
| `finalize` | Stores the answer, resolves citations and extracts quick-reply options. | — |

Every node streams events (`step`, `route`, `retrieval`, `llm`, `tool`, `waiting`, `check`, `reply`) through `get_stream_writer`. The FastAPI server relays them as Server-Sent Events, and the same events are saved per turn. The CLI, the example generator and the web page all consume this one stream.

### Why it is built this way

**LangGraph for orchestration, the Groq SDK for the model call.**
The graph makes the flow explicit and testable:
- each node is a small function;
- conditional edges carry the decisions;
- a SQLite checkpointer persists every conversation;
- `recursion_limit` and an iteration counter bound the loops.

I kept the model call on the plain Groq SDK instead of a LangChain `ChatModel`, for two reasons. The node controls rate-limit waits itself, so it can tell the user how long until the limit resets. And the tests can inject a fake client. Messages live in state as plain API-format dicts, so they serialize without conversion.

**RAG even though the manual is only ~2k tokens.**
The binding constraint is not context size but Groq's free tier: **8,000 tokens per request and per minute**. With the full manual in the system prompt, every call cost about 4.5k tokens before any history. Sending only the 3 relevant sections:
- brings a typical call down to about 2.5k tokens, which leaves room for history and tool results;
- makes every policy answer citable down to the section.

Retrieval is designed for a tiny Portuguese corpus:
- **Chunking:** one chunk per `##`/`###` section, with the section path prepended to the text. This is "contextual retrieval" without an LLM.
- **Text normalization:** accent folding, Portuguese stop-words and 5-letter prefix stemming, so "devolução" and "devolver" match.
- **Synonym expansion:** maps customer wording to manual vocabulary ("abrem domingo" → "horário funcionamento").
- **Ranking:** BM25 and dense rankings fused with Reciprocal Rank Fusion. If the embedding model is unavailable, search falls back to BM25 alone.
- **Relevance filter:** sections with no lexical or semantic match are dropped. An off-topic question retrieves *nothing*, and Lia says she will check with the team instead of guessing.

The store card (address, contacts, hours) and section 7 (service guidelines, which are instructions to Lia herself) stay fixed in the prompt.

**Traditional ML in the router.**
Before spending tokens, the graph has to decide whether the message needs the manual. Under-retrieving leaves the model without the rule; over-retrieving burns the token budget. A TF-IDF + logistic-regression classifier:
- decides in microseconds, at zero API cost;
- copes with typos and slang ("vcs", "qnt") thanks to character n-grams;
- supports multi-label messages ("quanto fica o C40 no PIX?" is product *and* policy);
- trains in under a second at startup from `datasets/intencoes.csv` (186 labeled sentences), so no binary model file is committed.

**Guardrails in code, not in extra LLM calls.**
Every extra call eats the same 8k budget, so the checks are deterministic: regex, Luhn and CPF check digits, and set membership. They cost nothing and are unit-tested. The output check catches the most damaging failure for a store: a made-up price or protocol number.

**Business math stays in Python.**
Python computes:
- the PIX 5% discount (not stackable with promotions);
- the installment caps (minimum installment per range);
- the after-sale deadlines (7-day regret, 30-day defect, 90-day legal warranty);
- the store's open or closed status.

The prompt tells the model to repeat those values, not recompute them, and `verify` enforces it.

**LGPD by construction.**
- The model never sees the customer table. `consultar_pedido` releases an order only when the order number *and* the registered e-mail or phone match.
- A missing order and a wrong contact return the same denial, so the existence of an order is not leaked.
- E-mails and phones are masked (`l***@jmail.com`) in every event sent to the UI.

**Prompt strategy.**
- A persona with tone rules.
- A `<fontes>` block that says which source to trust for which kind of question.
- Explicit rules for promotions, out-of-stock items, accessories and scope.
- Retrieved sections injected as a system message *right before* the current user message, so the prompt prefix (instructions + history) stays identical between calls and cacheable.
- Citations as `[§x]` markers, which the UI turns into buttons and the CLI strips.
- Optional `[[opções: A | B]]` quick replies.

**Model: `openai/gpt-oss-120b` on Groq.**
An open-weight model, good at tool use, with a free tier. It can be swapped with `EMPORIO_MODEL`.

**Interface without "AI slop".**
Plain HTML/CSS/JS served by FastAPI, with no build step. The visual direction follows the [unslop](https://github.com/heydamir/unslop) method:
- a neutral palette with Artefact's navy, and pink only for what links the two columns (citations, active node, turn numbers);
- IBM Plex type and tabular numbers;
- no gradients, glows or emoji in the chrome;
- real step names instead of "typing" dots.

It also handles accessibility: `role="log"`, a polite live region, keyboard tabs and `prefers-reduced-motion`.

**Fixed clock.**
The store data ends in March 2026. `EMPORIO_NOW` pins Lia's clock (default 2026-03-25 10:00), so deadlines and opening hours make sense and conversations are reproducible.

## Evaluation

Offline, no LLM calls: `uv run python scripts/avaliar.py` writes [docs/avaliacao.md](docs/avaliacao.md).

**Intent router, held-out set** (91 new sentences written after the model and the keyword list existed):

| Router | micro-F1 | macro-F1 | exact match |
|---|---|---|---|
| ML (TF-IDF + logistic regression) | **0.76** | **0.76** | **64%** |
| Keyword rules | 0.61 | 0.59 | 52% |

In cross-validation on the training set the keyword rules scored *higher* (0.77 vs 0.69). Only the held-out set exposed that I had written the keywords while looking at those very sentences, which is a textbook case of overfitting by hand. Combining both as features did not help on the held-out set, so the router uses the ML model alone, with keywords kept only as a fallback.

**Manual retrieval** (38 questions, each with the expected section):

| Search | top-1 | top-3 | MRR |
|---|---|---|---|
| BM25 | 84% | 97% | 0.91 |
| Embeddings only | 68% | 82% | 0.75 |
| **Hybrid (RRF)** | **89%** | **97%** | **0.94** |

**Tests** (`uv run pytest`, 60 tests, ~4 s, no network) cover:
- business rules;
- every graph path, using a fake Groq client: rate-limit wait, malformed-generation retry, turn rollback on error, invented price sent back and corrected, card number never reaching the model, contacts masked in events, conversation surviving a restart;
- retrieval hit-rate;
- the classifier beating keywords on the held-out set;
- guardrails;
- the web API.

## Example conversations

Generated against the real model by `scripts/gerar_conversas.py`. 01, 03 and 04 come from the current graph, and lines starting with `>` show its path. 02 and 05 come from the first version of the agent (see the notes in each file). Each file ends with the scenario's expected outcome as a checklist. Each one also opens in the UI at `/?sessao=exemplo-NN`.

| Example | What it shows |
|---|---|
| [01 · Search and payment](conversas/01-busca-e-pagamento.md) | Price-range search, PIX and installments computed in code, quick-reply options |
| [02 · Promotion that doesn't exist](conversas/02-promocao-inexistente.md) | Transparency when the customer quotes an inactive promotion. **Known error, kept on purpose:** the second answer says no guitar is on sale, but two are. It was generated by the first version, the fix is in the code, and the daily Groq quota ran out before it could be regenerated (note in the file) |
| [03 · Order, exchange and warranty](conversas/03-pedido-troca-e-garantia.md) | **Non-trivial:** identity check, deadlines from code, manual sections retrieved and cited, ticket for the team |
| [04 · Security and LGPD](conversas/04-seguranca-e-lgpd.md) | Wrong contact reveals nothing, card number stripped before the model, injection attempt |
| [05 · Empty category and opening hours](conversas/05-fora-do-catalogo-e-horario.md) | Empty category (no invented models), accessories out of scope, opening hours. Generated by the first version of the agent |

## Assumptions about the data

- The manual has a few contradictions: two phone numbers, two e-mails, and two installment rules. I picked one version of each and explained why in the "Notas de curadoria" at the end of `knowledge/politicas.md`.
- Orders have no real delivery date, only the estimated one, so the estimated date counts as the receipt date for the 7-day regret window.
- Some categories (wind instruments) exist but have no products. Lia says so instead of inventing models.
- When a product has more than one active promotion, the largest discount wins.
- The intent datasets in `datasets/` were written for this case and are synthetic. They are small (186 + 91 sentences), so the router numbers are indicative, not production estimates.

## Limitations and next steps

- **Slow on the free tier.** An answer that uses tools takes 5 to 60 seconds, because the agent waits for Groq's per-minute token limit. A paid tier would bring this down to a few seconds.
- **The free tier also caps tokens per day (200k).** A heavy day of testing runs out. The agent waits out the per-minute limit, but fails fast with a clear message when the wait is the daily one (minutes or hours) instead of leaving the customer hanging.
- **Human handoff writes to a file** (`.handoffs/chamados.jsonl`). The next step is LangGraph's `interrupt()` so an operator can take over the thread, plus a real ticketing integration.
- **The router learns from 186 sentences.** Production traffic, plus the 👍/👎 feedback already being collected, would be the training set. The same pipeline could flag dissatisfaction to prioritize tickets.
- **The output check covers values, protocols and citations**, but not claims phrased in words ("the warranty covers drops"). An LLM-as-judge would catch those, at the cost of another call per turn.
- **Very long conversations** are trimmed to the last turns that fit the budget. Summarizing older turns is the next step.
- **No authentication on the web UI.** Conversations live in a local SQLite file.
- **Holidays are not considered** in opening hours.
- **The final answer arrives whole.** The steps stream in real time, but the text itself is not streamed token by token.

## Use of AI coding assistants

I used **Claude Code** (Anthropic) as a pair programmer throughout. The workflow:

1. **Reading the case.** It read the PDF and the CSVs and listed inconsistencies in the data (the contradictions above). I decided how to resolve each one.
2. **Skeleton first, then iterations driven by real runs.** The first version was a hand-written tool-calling loop. Running real conversations surfaced problems that I then had it fix, each in its own commit:
   - Groq's 8k limit (413 errors);
   - the model doing its own date math (wrong number of days);
   - tickets opened without the customer's consent.
3. **Research before the redesign.** Before moving to LangGraph + RAG, I had it run two parallel web-research subagents:
   - one on current LangGraph APIs, RAG patterns for small Portuguese corpora and deterministic guardrails;
   - one on interface patterns for agent transparency: citations, step traces, structured cards, accessibility.

   The architecture and UI decisions above come from those reports, filtered by the 8k-token constraint.
4. **Evaluation as the arbiter.** For the ML router, the first cross-validation result made keywords look better. I had it write a separate held-out set, which reversed the conclusion, and the README reports both numbers.
5. **Visual direction.** The [unslop](https://github.com/heydamir/unslop) skill by Damir Sokolovsky (CC BY 4.0) guided the interface.

Architecture choices, assumptions and final review were mine. The commit history shows the progression step by step.

## Project layout

| Path | What it is |
|---|---|
| `src/emporio_agent/graph.py` | The LangGraph graph: nodes, edges, rate-limit handling, context window |
| `src/emporio_agent/agent.py` | Facade: runs the graph, SQLite checkpoint, rollback of failed turns, event log |
| `src/emporio_agent/rag.py` | Chunking, BM25, embeddings, RRF, query expansion |
| `src/emporio_agent/intent.py` | TF-IDF + logistic regression router (and the keyword baseline) |
| `src/emporio_agent/guardrails.py` | Input screening, PII redaction and masking, answer check |
| `src/emporio_agent/data.py` | CSV loading and all business math |
| `src/emporio_agent/tools.py` | Tool schemas and dispatch |
| `src/emporio_agent/prompts.py` | System prompt |
| `src/emporio_agent/web.py`, `static/` | FastAPI + SSE, and the page (vanilla JS) |
| `src/emporio_agent/scenarios.py` | Demo scenarios and their expected outcomes |
| `knowledge/politicas.md` | The policy manual transcribed from the PDF |
| `datasets/` | Labeled intents (train / held-out) and retrieval golden questions |
| `scripts/` | Example generator and offline evaluation |
| `tests/` | 60 offline tests |
