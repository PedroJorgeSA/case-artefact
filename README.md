# Empório da Música: assistente de atendimento

Este projeto é a **Lia**, uma assistente virtual para a Empório da Música, uma loja (fictícia) de instrumentos musicais em Campo Grande/MS.

Ela conversa com o cliente por texto, como no WhatsApp, e consegue:

- procurar instrumentos e dizer preço, estoque e promoções;
- calcular o preço no PIX e em quantas vezes dá para parcelar;
- consultar um pedido (só se o cliente provar que é dele);
- responder dúvidas sobre trocas, garantia, frete e horários;
- passar o caso para uma pessoa da equipe quando precisa.

## Como rodar

Você precisa do [uv](https://docs.astral.sh/uv/) instalado e de uma chave grátis do Groq ([console.groq.com/keys](https://console.groq.com/keys)).

```bash
cp .env.example .env     # depois, cole sua chave em GROQ_API_KEY
uv sync                  # instala tudo
uv run emporio           # abre o chat no terminal
```

Outros comandos úteis:

```bash
uv run emporio --debug           # mostra quais ferramentas a Lia usou em cada resposta
uv run emporio --session abc123  # continua uma conversa antiga
uv run pytest                    # roda os testes (não gastam chamadas de IA)
```

Para sair do chat, digite `sair`.

## Como funciona, em uma imagem

```
Cliente escreve
     │
     ▼
  Agente  ──────►  Modelo de IA (Groq)
     ▲                  │
     │      "preciso buscar violões até R$1000"
     │                  ▼
     └────────  Ferramentas  ──►  Planilhas da loja (CSV)
```

1. O cliente manda uma mensagem.
2. O modelo de IA lê a mensagem e decide se precisa de algum dado da loja.
3. Se precisar, ele pede para uma **ferramenta** (uma função Python) buscar o dado.
4. A ferramenta lê as planilhas, faz as contas e devolve o resultado.
5. O modelo usa esse resultado para escrever a resposta.

### Onde fica cada coisa

| Arquivo | O que faz |
|---|---|
| `src/emporio_agent/data.py` | Lê as planilhas e faz todas as contas (PIX, parcelas, prazos de troca, horário da loja) |
| `src/emporio_agent/tools.py` | As 5 ferramentas que a IA pode usar |
| `src/emporio_agent/prompts.py` | As instruções da Lia: jeito de falar, regras e o manual da loja |
| `src/emporio_agent/agent.py` | O "vai e volta" entre a IA e as ferramentas |
| `src/emporio_agent/cli.py` | O chat no terminal |
| `knowledge/politicas.md` | O manual da loja passado do PDF para texto |
| `tests/test_data.py` | Testes das regras de negócio |

As 5 ferramentas são: `buscar_produtos`, `detalhes_produto`, `listar_categorias`, `consultar_pedido` e `encaminhar_para_atendente`.

## Por que foi feito assim

**As contas são feitas em Python, não pela IA.**
Modelos de linguagem erram conta com facilidade. Por isso o desconto do PIX, o número de parcelas e os prazos de troca são calculados no código e conferidos pelos testes. A IA só repete o resultado.

**Ferramentas para os dados, manual inteiro nas instruções.**
São só 65 produtos e 20 pedidos, então uma busca simples em Python resolve, sem banco vetorial (RAG). O manual da loja é curto (cerca de 3 mil tokens) e cabe inteiro nas instruções da Lia, então ela sempre tem a regra completa à mão.

**A IA nunca vê a lista de clientes.**
Para consultar um pedido, a ferramenta exige o número do pedido **e** o e-mail ou telefone do dono. Se não bater, não mostra nada. Isso protege os dados pessoais (LGPD), mesmo que alguém tente enganar a IA.

**Modelo `openai/gpt-oss-120b` no Groq.**
É um modelo aberto, bom em usar ferramentas, e o Groq tem plano gratuito. Dá para trocar o modelo no `.env` (`EMPORIO_MODEL`).

**Data fixa para testes.**
Os dados da loja vão só até março de 2026. A variável `EMPORIO_NOW` "congela" o relógio da Lia numa data (por padrão 25/03/2026 às 10h), assim as respostas sobre prazos e horário fazem sentido e dá para repetir as conversas.

## O que eu assumi sobre os dados

- O manual tem algumas contradições (dois telefones, dois e-mails, regras de parcelamento diferentes). Escolhi uma versão de cada e expliquei o motivo no fim de `knowledge/politicas.md`.
- Os pedidos não têm a data real de entrega, só a data prevista. Uso a data prevista para contar os 7 dias de arrependimento.
- Algumas categorias (como instrumentos de sopro) existem, mas não têm produto. A Lia diz que não tem no momento, sem inventar modelos.

## Limitações

- **Lento no plano grátis do Groq.** O limite é de 8 mil tokens por minuto e cada chamada usa uns 3,5 mil. Quando a Lia precisa de várias ferramentas numa resposta, ela pode demorar perto de um minuto esperando o limite liberar.
- **Os chamados para atendentes vão para um arquivo** (`.handoffs/chamados.jsonl`), não para um sistema de verdade.
- **As conversas ficam salvas em arquivos JSON** (`.sessions/`). Numa loja real, isso iria para um banco de dados.
- **Feriados não são considerados** no horário de funcionamento.

## Uso de assistentes de IA

Usei o Claude Code (Anthropic) como par de programação. Ele ajudou a:

- ler o PDF do desafio e as planilhas, e apontar inconsistências nos dados;
- montar o esqueleto do projeto, as ferramentas e os testes;
- trocar a integração para o Groq e escrever este README.

As decisões de arquitetura, as premissas e a revisão final foram minhas.

## Exemplos de conversa

Ficam na pasta [`conversas/`](conversas/). *(em construção)*
