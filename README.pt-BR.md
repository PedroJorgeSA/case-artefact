# Empório da Música · agente de atendimento "Lia"

> 🇺🇸 English version: [README.md](README.md)

A **Lia** é um protótipo de agente de atendimento para a Empório da Música, uma loja fictícia de instrumentos musicais em Campo Grande (MS). O cliente conversa com ela por texto, como no WhatsApp. Ela responde com os dados reais da loja (planilhas de produtos, promoções e pedidos) e com o manual de políticas.

![Conversa à esquerda, o percurso de cada mensagem no grafo à direita](docs/img/desktop.png)

*À esquerda, a conversa como o cliente vê. À direita, o que aconteceu por dentro, nó a nó: a intenção detectada, as seções do manual recuperadas (com as notas), as consultas, os tokens de cada chamada ao modelo e a checagem da resposta.*

O que a Lia faz:

- procura no catálogo e diz preço, estoque e promoções ativas;
- calcula em código o preço no PIX e o parcelamento máximo sem juros;
- consulta um pedido, mas só depois de o cliente provar que é dele (número + e-mail ou telefone cadastrado);
- responde sobre troca, garantia, frete, pagamento e horário a partir do manual, **citando a seção exata** (`[§4.1]`);
- abre um chamado para a equipe, com um resumo que dispensa reler a conversa.

## Como rodar

Você precisa do [uv](https://docs.astral.sh/uv/) (`brew install uv` no Mac) e de uma chave grátis do Groq em [console.groq.com/keys](https://console.groq.com/keys).

```bash
cp .env.example .env        # depois cole a chave em GROQ_API_KEY=
uv sync
uv run emporio-web          # abra http://127.0.0.1:8000
```

Na primeira vez, o servidor baixa um modelo pequeno de embeddings multilíngue (~220 MB, guardado em `.cache/`). Com `EMPORIO_EMBEDDINGS=off` ele não é baixado e a busca no manual usa só BM25.

Outros comandos:

```bash
uv run emporio --debug                     # chat no terminal, mostrando o caminho de cada mensagem no grafo
uv run pytest                              # 57 testes, sem internet (cliente Groq falso, só BM25)
uv run python scripts/avaliar.py           # avaliação offline do roteador e da busca no manual
uv run python scripts/gerar_conversas.py   # roda de novo as 5 conversas de exemplo com o modelo de verdade
```

## Como usar a interface

1. **Escreva como um cliente** ou clique numa das perguntas de exemplo.
2. **Acompanhe a aba "Percurso".** Cada mensagem ganha um bloco com:
   - a linha do tempo por nó do grafo;
   - as probabilidades de cada intenção;
   - as seções do manual recuperadas, com as notas de BM25, similaridade e RRF e as citadas em destaque;
   - cada consulta com o resultado desenhado;
   - os tokens de entrada e saída contra o limite de 8 mil por chamada;
   - o veredito da checagem.

   O mini grafo no topo acende o caminho que a mensagem fez.
3. **Clique numa citação** como `§4.1` numa resposta. A aba "Manual" abre naquela seção.
4. **Rode um cenário.** A aba "Cenários" repete cinco conversas roteirizadas e confere o resultado esperado: ferramentas chamadas, seções citadas e dados que *não* podem aparecer.
5. **Avalie uma resposta.** "Não ajudou" pede o motivo (valor errado, regra errada…), e a avaliação vai para `.feedback/`.

Recarregar a página redesenha as duas colunas, porque todos os eventos ficam salvos com a conversa. `?sessao=exemplo-03` abre um dos exemplos.

| Citação abre o manual | Cenários com verificação | Celular |
|---|---|---|
| ![](docs/img/manual.png) | ![](docs/img/cenarios.png) | ![](docs/img/celular.png) |

## Arquitetura

```
            ┌──────────── StateGraph do LangGraph, com checkpoint em SQLite ─────────┐
 mensagem ─►│ guard ─► route ─┬─► retrieve ─┐                                         │
            │                 └─────────────┴─► agent ◄──► tools                     │
            │                                     │                                  │
            │                                     ▼                                  │
            │                                   verify ──(refaz 1 vez)──► agent      │
            │                                     │                                  │
            │                                     ▼                                  │
            │                                  finalize ─► resposta + citações + opções
            └─────────────────────────────────────────────────────────────────────────┘
```

| Nó | O que faz | Como |
|---|---|---|
| `guard` | Sinaliza tentativa de mudar as regras (prompt injection). Remove número de cartão (Luhn) e CPF (dígitos verificadores) **antes** de chegarem ao modelo ou ao histórico. | Regex + checagem matemática, sem LLM |
| `route` | Decide se a mensagem precisa do manual. | **ML tradicional**: TF-IDF (n-gramas de 2 a 5 caracteres + palavras) → regressão logística um-contra-todos |
| `retrieve` | Busca as 3 seções mais relevantes do manual. Pergunta curta ("e no PIX?") herda a mensagem anterior como consulta. | RAG híbrido: BM25 + embeddings multilíngues, fundidos por RRF |
| `agent` | Chama o LLM com as ferramentas. Espera o limite do Groq e avisa a interface. | SDK do Groq, `openai/gpt-oss-120b`, function calling |
| `tools` | Busca no catálogo, detalhes, categorias, consulta de pedido (com verificação LGPD), chamado para a equipe e `consultar_manual` (RAG sob demanda quando o roteador errou). | pandas sobre os CSVs; todas as contas em Python |
| `verify` | Todo valor em R$, protocolo e citação `[§x]` do rascunho precisa aparecer nas fontes do turno. Se não aparece, o rascunho volta ao modelo uma vez, com o problema. | Determinístico, sem chamada extra ao LLM |
| `finalize` | Grava a resposta, resolve as citações e separa as opções de resposta rápida. | — |

Cada nó emite eventos (`step`, `route`, `retrieval`, `llm`, `tool`, `waiting`, `check`, `reply`) com `get_stream_writer`. O FastAPI repassa esses eventos por Server-Sent Events e os mesmos eventos ficam salvos por turno. O CLI, o gerador de exemplos e a página usam esse mesmo fluxo.

### Por que foi feito assim

**LangGraph na orquestração, SDK do Groq na chamada ao modelo.**
O grafo deixa o fluxo explícito e testável:
- cada nó é uma função pequena;
- as decisões ficam nas arestas condicionais;
- um checkpointer SQLite guarda cada conversa;
- `recursion_limit` e um contador de iterações limitam os laços.

Mantive a chamada ao modelo no SDK do Groq, e não num `ChatModel` do LangChain, por dois motivos. O nó controla as esperas por limite de taxa e consegue dizer ao cliente quanto falta. E os testes podem usar um cliente falso. As mensagens ficam no estado como dicts no formato da API, que serializam sem conversão.

**RAG mesmo com um manual de ~2 mil tokens.**
O limite que importa não é o tamanho do contexto, mas o plano gratuito do Groq: **8 mil tokens por chamada e por minuto**. Com o manual inteiro no prompt, cada chamada gastava cerca de 4,5 mil tokens antes de qualquer histórico. Mandar só as 3 seções relevantes:
- baixa uma chamada típica para cerca de 2,5 mil tokens e libera espaço para histórico e resultados;
- permite citar cada resposta de política até a seção.

A busca foi pensada para um corpus pequeno em português:
- **Divisão:** um trecho por seção `##`/`###`, com o caminho da seção no começo do texto. É "contextual retrieval" sem LLM.
- **Normalização:** sem acentos, sem stop-words e com radical por prefixo de 5 letras, para "devolução" casar com "devolver".
- **Sinônimos:** expansão das palavras do cliente para o vocabulário do manual ("abrem domingo" → "horário funcionamento").
- **Ranking:** os rankings de BM25 e de embeddings são fundidos por Reciprocal Rank Fusion. Sem o modelo de embeddings, a busca segue só com BM25.
- **Filtro de relevância:** seção sem relação por palavra nem por sentido é descartada. Uma pergunta fora do tema recupera *nada*, e a Lia diz que vai confirmar com a equipe em vez de chutar.

A ficha da loja (endereço, contatos, horário) e a seção 7 (diretrizes de atendimento, que são instruções para a própria Lia) ficam fixas no prompt.

**ML tradicional no roteador.**
Antes de gastar tokens, o grafo precisa saber se a mensagem precisa do manual. Errar para menos deixa o modelo sem a regra; errar para mais gasta o limite. Um classificador TF-IDF + regressão logística:
- decide em microssegundos, sem custo de API;
- aguenta erro de digitação e gíria ("vcs", "qnt") graças aos n-gramas de caracteres;
- aceita mais de uma intenção ("quanto fica o C40 no PIX?" é produto *e* política);
- treina em menos de um segundo ao subir, a partir de `datasets/intencoes.csv` (186 frases rotuladas), então nenhum arquivo de modelo vai para o repositório.

**Guardrails em código, não em chamadas extras ao LLM.**
Cada chamada extra gasta do mesmo limite de 8 mil, então as checagens são determinísticas: regex, Luhn, dígitos do CPF e comparação de conjuntos. Custam zero e têm testes. A checagem de saída pega a falha que mais prejudica uma loja: preço ou protocolo inventado.

**As contas ficam em Python.**
Python calcula:
- o desconto de 5% no PIX (que não soma com promoção);
- o limite de parcelas (parcela mínima por faixa);
- os prazos de pós-venda (7 dias de arrependimento, 30 de defeito, 90 de garantia legal);
- se a loja está aberta.

O prompt manda o modelo repetir esses valores sem refazer as contas, e o `verify` confere.

**LGPD por construção.**
- O modelo nunca vê a tabela de clientes. `consultar_pedido` só libera um pedido quando o número *e* o e-mail ou telefone cadastrado batem.
- Pedido inexistente e contato errado recebem a mesma negativa, então a resposta não revela se o pedido existe.
- E-mails e telefones aparecem mascarados (`l***@jmail.com`) em todo evento enviado à interface.

**Estratégia de prompt.**
- Persona com regras de tom.
- Um bloco `<fontes>` que diz em qual fonte confiar para cada tipo de pergunta.
- Regras explícitas para promoção, falta de estoque, acessórios e escopo.
- Trechos do manual numa mensagem de sistema *logo antes* da mensagem atual, para o começo do prompt (instruções + histórico) ficar igual entre chamadas e aproveitável em cache.
- Citações como `[§x]`, que a interface transforma em botões e o CLI remove.
- Opções `[[opções: A | B]]` para resposta rápida.

**Modelo `openai/gpt-oss-120b` no Groq.**
Modelo de pesos abertos, bom em usar ferramentas, com plano gratuito. Dá para trocar em `EMPORIO_MODEL`.

**Interface sem cara de "feita por IA".**
HTML, CSS e JavaScript puros servidos pelo FastAPI, sem build. O visual segue o método [unslop](https://github.com/heydamir/unslop):
- paleta neutra com o azul-marinho da Artefact, e o rosa só no que liga as duas colunas (citações, nó ativo, número do turno);
- fontes IBM Plex e números tabulares;
- sem gradientes, brilhos ou emojis na interface;
- nomes reais das etapas em vez de "digitando…".

Também cuida de acessibilidade: `role="log"`, região de anúncio educada, abas por teclado e `prefers-reduced-motion`.

**Relógio fixo.**
Os dados da loja vão até março de 2026. `EMPORIO_NOW` fixa o relógio da Lia (padrão 25/03/2026 às 10h), para prazos e horários fazerem sentido e as conversas serem reproduzíveis.

## Avaliação

Offline, sem chamar o LLM: `uv run python scripts/avaliar.py` grava [docs/avaliacao.md](docs/avaliacao.md).

**Roteador de intenção, conjunto de teste separado** (91 frases novas, escritas depois do modelo e das palavras-chave):

| Roteador | F1 micro | F1 macro | acerto exato |
|---|---|---|---|
| ML (TF-IDF + regressão logística) | **0,76** | **0,76** | **64%** |
| Palavras-chave | 0,61 | 0,59 | 52% |

Na validação cruzada no próprio treino, as palavras-chave ficaram *na frente* (0,77 contra 0,69). Só o teste separado mostrou que eu tinha escrito as palavras-chave olhando aquelas mesmas frases, um caso clássico de sobreajuste feito à mão. Usar as palavras-chave como atributo do modelo não ajudou no teste separado. Por isso o roteador usa só o ML, e as palavras-chave ficam como reserva.

**Busca no manual** (38 perguntas, cada uma com a seção esperada):

| Busca | top-1 | top-3 | MRR |
|---|---|---|---|
| BM25 | 84% | 97% | 0,91 |
| Só embeddings | 68% | 82% | 0,75 |
| **Híbrida (RRF)** | **89%** | **97%** | **0,94** |

**Testes** (`uv run pytest`, 57 testes, ~4 s, sem internet) cobrem:
- as regras de negócio;
- todos os caminhos do grafo, com cliente Groq falso: espera por limite, nova tentativa após saída malformada, desfazer turno com erro, preço inventado devolvido e corrigido, cartão que nunca chega ao modelo, contatos mascarados, conversa que sobrevive a um reinício;
- o acerto da busca;
- o classificador vencendo as palavras-chave no teste separado;
- os guardrails;
- a API web.

## Exemplos de conversa

Gerados com o modelo de verdade por `scripts/gerar_conversas.py`. Linhas com `>` mostram o caminho no grafo. Cada arquivo termina com o resultado esperado do cenário em forma de checklist. Cada um também abre na interface em `/?sessao=exemplo-NN`.

| Exemplo | O que mostra |
|---|---|
| [01 · Busca e pagamento](conversas/01-busca-e-pagamento.md) | Busca por faixa de preço, PIX e parcelas calculados em código, opções de resposta rápida |
| [02 · Promoção que não existe](conversas/02-promocao-inexistente.md) | Transparência quando o cliente cita uma promoção inativa |
| [03 · Pedido, troca e garantia](conversas/03-pedido-troca-e-garantia.md) | **Não trivial:** verificação de identidade, prazos calculados em código, seções do manual recuperadas e citadas, chamado para a equipe |
| [04 · Segurança e LGPD](conversas/04-seguranca-e-lgpd.md) | Contato errado não revela nada, cartão removido antes do modelo, tentativa de manipulação |
| [05 · Manual, endereço e horário](conversas/05-manual-endereco-e-horario.md) | Perguntas sugeridas no enunciado respondidas pelo manual com citação; categoria vazia e acessórios |

## O que eu assumi sobre os dados

- O manual tem algumas contradições: dois telefones, dois e-mails e duas regras de parcelamento. Escolhi uma versão de cada e expliquei o motivo nas "Notas de curadoria", no fim de `knowledge/politicas.md`.
- Os pedidos não têm data real de entrega, só a prevista. Uso a data prevista como data de recebimento para contar os 7 dias de arrependimento.
- Algumas categorias (instrumentos de sopro) existem, mas não têm produto. A Lia diz isso sem inventar modelos.
- Quando um produto tem mais de uma promoção ativa, vale a de maior desconto.
- As frases de intenção em `datasets/` foram escritas para este case e são sintéticas. Como são poucas (186 + 91), os números do roteador são indicativos, não uma estimativa de produção.

## Limitações e próximos passos

- **Lento no plano grátis.** Uma resposta com consultas leva de 5 a 60 segundos, porque o agente espera o limite de tokens por minuto do Groq. Num plano pago, cai para poucos segundos.
- **O plano grátis também limita tokens por dia (200 mil).** Um dia inteiro de testes esgota o limite. O agente espera o limite por minuto, mas, quando a espera é a do limite diário (minutos ou horas), falha na hora com uma mensagem clara, em vez de deixar o cliente esperando.
- **O chamado para a equipe vai para um arquivo** (`.handoffs/chamados.jsonl`). O próximo passo é o `interrupt()` do LangGraph, para um atendente assumir a conversa, e a integração com um sistema de chamados de verdade.
- **O roteador aprende com 186 frases.** As mensagens reais, junto com as avaliações 👍/👎 que já são coletadas, seriam o conjunto de treino. O mesmo pipeline poderia detectar insatisfação para priorizar chamados.
- **A checagem cobre valores, protocolos e citações**, mas não afirmações escritas em palavras ("a garantia cobre queda"). Um LLM-juiz pegaria isso, ao custo de mais uma chamada por turno.
- **Conversas muito longas** são cortadas aos últimos turnos que cabem no limite. Resumir os turnos antigos é o próximo passo.
- **A interface web não tem login.** As conversas ficam num arquivo SQLite local.
- **Feriados não são considerados** no horário.
- **A resposta final chega inteira.** As etapas aparecem em tempo real, mas o texto não chega aos poucos.

## Uso de assistentes de IA

Usei o **Claude Code** (Anthropic) como par de programação do começo ao fim. O fluxo:

1. **Leitura do case.** Ele leu o PDF e os CSVs e listou as inconsistências dos dados (as contradições acima). Eu decidi como resolver cada uma.
2. **Esqueleto primeiro, depois iterações guiadas por testes reais.** A primeira versão era um loop de function calling escrito à mão. Rodar conversas de verdade revelou problemas que pedi para ele corrigir, cada um no seu commit:
   - o limite de 8 mil do Groq (erros 413);
   - o modelo fazendo conta de datas sozinho (número de dias errado);
   - chamado aberto sem o aceite do cliente.
3. **Pesquisa antes da remodelação.** Antes de ir para LangGraph + RAG, pedi duas pesquisas na web em paralelo, feitas por subagentes:
   - uma sobre as APIs atuais do LangGraph, padrões de RAG para corpus pequeno em português e guardrails determinísticos;
   - outra sobre padrões de interface para transparência de agentes: citações, rastro de etapas, cartões estruturados, acessibilidade.

   As decisões de arquitetura e de interface acima saíram desses relatórios, filtradas pelo limite de 8 mil tokens.
4. **A avaliação decide.** No roteador de ML, a primeira validação cruzada fazia as palavras-chave parecerem melhores. Pedi um conjunto de teste separado, que inverteu a conclusão, e o README mostra os dois números.
5. **Direção visual.** A skill [unslop](https://github.com/heydamir/unslop), de Damir Sokolovsky (CC BY 4.0), guiou a interface.

As escolhas de arquitetura, as premissas e a revisão final foram minhas. O histórico de commits mostra a evolução passo a passo.

## Onde fica cada coisa

| Caminho | O que é |
|---|---|
| `src/emporio_agent/graph.py` | O grafo do LangGraph: nós, arestas, esperas por limite, janela de contexto |
| `src/emporio_agent/agent.py` | Fachada: roda o grafo, checkpoint SQLite, desfaz turnos com erro, log de eventos |
| `src/emporio_agent/rag.py` | Divisão do manual, BM25, embeddings, RRF, expansão de sinônimos |
| `src/emporio_agent/intent.py` | Roteador TF-IDF + regressão logística (e a base por palavras-chave) |
| `src/emporio_agent/guardrails.py` | Triagem da entrada, remoção e mascaramento de dados pessoais, checagem da resposta |
| `src/emporio_agent/data.py` | Leitura dos CSVs e todas as contas |
| `src/emporio_agent/tools.py` | Esquemas e despacho das ferramentas |
| `src/emporio_agent/prompts.py` | Prompt de sistema |
| `src/emporio_agent/web.py`, `static/` | FastAPI + SSE e a página (JavaScript puro) |
| `src/emporio_agent/scenarios.py` | Cenários de demonstração e o resultado esperado |
| `knowledge/politicas.md` | O manual de políticas transcrito do PDF |
| `datasets/` | Intenções rotuladas (treino / teste separado) e perguntas de referência da busca |
| `scripts/` | Gerador de exemplos e avaliação offline |
| `tests/` | 57 testes offline |
