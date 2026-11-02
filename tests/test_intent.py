"""Testes do classificador de intenção (ML tradicional)."""

import numpy as np
from sklearn.metrics import f1_score
from sklearn.preprocessing import MultiLabelBinarizer

from emporio_agent.intent import DATASET, LABELS, keyword_intents, load_dataset, predict


def test_routes_clear_messages():
    assert "politica" in predict("vcs abrem domingo?").labels
    assert "produto" in predict("qnt custa o violao yamaha").labels
    assert "pedido" in predict("meu pedido 7 não chegou").labels
    assert predict("me conta uma piada").labels == ["fora_escopo"]
    assert predict("oi, tudo bem?").labels == ["saudacao"]


def test_scores_cover_every_label():
    prediction = predict("quanto fica o C40 no pix?")
    assert set(prediction.scores) == set(LABELS) and prediction.method == "ml"


def test_ml_beats_keywords_on_held_out_set():
    texts, labels = load_dataset(DATASET.with_name("intencoes_teste.csv"))
    binarizer = MultiLabelBinarizer(classes=LABELS).fit([LABELS])
    truth = binarizer.transform(labels)
    ml = f1_score(truth, binarizer.transform([predict(t).labels for t in texts]), average="micro")
    kw = f1_score(truth, binarizer.transform([keyword_intents(t) for t in texts]), average="micro")
    assert ml >= 0.7 and ml > kw


def test_training_and_test_sets_do_not_overlap():
    train, _ = load_dataset()
    test, _ = load_dataset(DATASET.with_name("intencoes_teste.csv"))
    assert not set(map(str.lower, train)) & set(map(str.lower, test))
    assert np.mean([len(t) for t in test]) > 5
