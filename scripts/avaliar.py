"""Avaliação offline (sem chamar o LLM) das duas peças que decidem o que o modelo recebe.

1. Classificador de intenção, ML (TF-IDF + regressão logística) contra
   palavras-chave: no conjunto de teste separado (datasets/intencoes_teste.csv,
   escrito depois, que nenhum dos dois viu) e em validação cruzada no conjunto
   de treino. As palavras-chave foram escritas olhando o treino, então só o
   teste separado mede de forma justa.
2. RAG do manual: acerto no top-1 e no top-3 e MRR para cada pergunta de
   datasets/perguntas_manual.csv, com BM25, só embeddings e híbrido (RRF).

Uso:
    uv run python scripts/avaliar.py            # imprime e grava docs/avaliacao.md
"""

import csv
from pathlib import Path

import numpy as np
from sklearn.metrics import f1_score
from sklearn.model_selection import KFold
from sklearn.preprocessing import MultiLabelBinarizer

from emporio_agent import config
from emporio_agent.intent import DATASET, LABELS, IntentClassifier, keyword_intents, load_dataset
from emporio_agent.rag import PolicyIndex, load_index

OUT = config.ROOT_DIR / "docs" / "avaliacao.md"
ML_NAME = "ML (TF-IDF + regressão logística)"
KW_NAME = "Palavras-chave"


def _intent_table(y_true_all, predictions) -> list[str]:
    binarizer = MultiLabelBinarizer(classes=LABELS).fit([LABELS])
    y_true = binarizer.transform(y_true_all)
    rows = ["| Método | F1 micro | F1 macro | Acerto exato |", "|---|---|---|---|"]
    for name, preds in predictions.items():
        y_pred = binarizer.transform(preds)
        exact = np.mean([set(p) == set(t) for p, t in zip(preds, y_true_all)])
        rows.append(
            f"| {name} | {f1_score(y_true, y_pred, average='micro'):.2f} | "
            f"{f1_score(y_true, y_pred, average='macro', zero_division=0):.2f} | {exact:.0%} |"
        )
    rows += ["", "| Intenção | " + " | ".join(f"F1 {n.split(' (')[0]}" for n in predictions) + " |", "|---|---|---|"]
    per_label = [f1_score(y_true, binarizer.transform(p), average=None, zero_division=0) for p in predictions.values()]
    for i, label in enumerate(LABELS):
        rows.append(f"| {label} | " + " | ".join(f"{scores[i]:.2f}" for scores in per_label) + " |")
    return rows


def evaluate_intents() -> list[str]:
    texts, labels = load_dataset()
    test_texts, test_labels = load_dataset(DATASET.with_name("intencoes_teste.csv"))
    clf = IntentClassifier(texts, labels)
    holdout = {
        ML_NAME: [clf.predict(t).labels for t in test_texts],
        KW_NAME: [keyword_intents(t) for t in test_texts],
    }

    y_true_cv, y_ml_cv, y_kw_cv = [], [], []
    for seed in range(3):
        for train, test in KFold(5, shuffle=True, random_state=seed).split(texts):
            fold = IntentClassifier([texts[i] for i in train], [labels[i] for i in train])
            for i in test:
                y_true_cv.append(labels[i])
                y_ml_cv.append(fold.predict(texts[i]).labels)
                y_kw_cv.append(keyword_intents(texts[i]))

    return [
        f"### Classificador de intenção: teste separado ({len(test_texts)} frases novas, treino com {len(texts)})",
        "",
        *_intent_table(test_labels, holdout),
        "",
        "### Classificador de intenção: validação cruzada no treino (5x, repetida 3 vezes)",
        "",
        "As palavras-chave foram escritas olhando estas frases, então aqui elas levam vantagem.",
        "",
        *_intent_table(y_true_cv, {ML_NAME: y_ml_cv, KW_NAME: y_kw_cv}),
    ]


def evaluate_rag() -> list[str]:
    with open(config.ROOT_DIR / "datasets" / "perguntas_manual.csv", encoding="utf-8") as f:
        golden = [(r["pergunta"], set(r["secoes_esperadas"].split("|"))) for r in csv.DictReader(f)]
    hybrid = load_index(True)
    variants = {"BM25": load_index(False), "Híbrido (BM25 + embeddings, RRF)": hybrid}
    if hybrid.dense:
        dense_only = PolicyIndex(hybrid.chunks, use_embeddings=False)
        dense_only.dense = hybrid.dense
        dense_only.bm25.scores = lambda terms: [0.0] * len(hybrid.chunks)
        variants = {"BM25": variants["BM25"], "Só embeddings": dense_only, **{k: v for k, v in variants.items() if k != "BM25"}}

    rows, misses = [], []
    for name, index in variants.items():
        top1 = top3 = rr = 0.0
        for question, expected in golden:
            ids = [h.chunk.id for h in index.search(question, k=5).hits]
            rank = next((i + 1 for i, c in enumerate(ids) if c in expected), None)
            top1 += rank == 1
            top3 += rank is not None and rank <= 3
            rr += 1 / rank if rank else 0
            if name.startswith("Híbrido") and (rank is None or rank > 3):
                misses.append(f"- \"{question}\" esperava §{'/'.join(expected)}, veio {ids[:3]}")
        n = len(golden)
        rows.append(f"| {name} | {top1 / n:.0%} | {top3 / n:.0%} | {rr / n:.2f} |")
    return [
        f"### Recuperação no manual ({len(golden)} perguntas com a seção esperada)",
        "",
        "| Busca | Acerto top-1 | Acerto top-3 | MRR |",
        "|---|---|---|---|",
        *rows,
        "",
        *(["Erros do híbrido no top-3:", "", *misses] if misses else ["O híbrido acerta todas no top-3."]),
    ]


def main() -> None:
    lines = [
        "# Avaliação offline",
        "",
        "Gerado por `scripts/avaliar.py`. Não usa o LLM: mede só o roteador e a busca no manual.",
        "",
        *evaluate_intents(),
        "",
        *evaluate_rag(),
        "",
    ]
    text = "\n".join(lines)
    print(text)
    Path(OUT).write_text(text, encoding="utf-8")
    print(f"\nsalvo em {OUT.relative_to(config.ROOT_DIR)}")


if __name__ == "__main__":
    main()
