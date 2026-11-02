# Avaliação offline

Gerado por `scripts/avaliar.py`. Não usa o LLM: mede só o roteador e a busca no manual.

### Classificador de intenção: teste separado (91 frases novas, treino com 186)

| Método | F1 micro | F1 macro | Acerto exato |
|---|---|---|---|
| ML (TF-IDF + regressão logística) | 0.76 | 0.76 | 64% |
| Palavras-chave | 0.61 | 0.59 | 52% |

| Intenção | F1 ML | F1 Palavras-chave |
|---|---|---|
| produto | 0.70 | 0.74 |
| pedido | 0.90 | 0.69 |
| politica | 0.77 | 0.69 |
| reclamacao | 0.69 | 0.42 |
| saudacao | 0.78 | 0.62 |
| fora_escopo | 0.73 | 0.39 |

### Classificador de intenção: validação cruzada no treino (5x, repetida 3 vezes)

As palavras-chave foram escritas olhando estas frases, então aqui elas levam vantagem.

| Método | F1 micro | F1 macro | Acerto exato |
|---|---|---|---|
| ML (TF-IDF + regressão logística) | 0.69 | 0.63 | 55% |
| Palavras-chave | 0.77 | 0.72 | 69% |

| Intenção | F1 ML | F1 Palavras-chave |
|---|---|---|
| produto | 0.77 | 0.88 |
| pedido | 0.84 | 0.79 |
| politica | 0.70 | 0.83 |
| reclamacao | 0.43 | 0.53 |
| saudacao | 0.66 | 0.71 |
| fora_escopo | 0.39 | 0.55 |

### Recuperação no manual (38 perguntas com a seção esperada)

| Busca | Acerto top-1 | Acerto top-3 | MRR |
|---|---|---|---|
| BM25 | 84% | 97% | 0.91 |
| Só embeddings | 68% | 82% | 0.75 |
| Híbrido (BM25 + embeddings, RRF) | 89% | 97% | 0.94 |

Erros do híbrido no top-3:

- "meu instrumento chegou amassado o que faço" esperava §5.2, veio ['1', '1.1', '4.4']
