"""Classificador de intenção com machine learning tradicional (scikit-learn).

O nó `route` do grafo precisa decidir, antes de chamar o LLM, se a mensagem
precisa de trechos do manual. Errar para menos deixa o modelo sem a regra;
errar para mais gasta tokens do limite do Groq. Um classificador clássico
resolve isso em microssegundos, sem custo de API:

- TF-IDF de n-gramas de caracteres (2 a 5, dentro das palavras), robusto a erro
  de digitação e gíria ("vcs", "qnt", "violao"), unido a TF-IDF de palavras;
- regressão logística um-contra-todos, porque uma mensagem pode ter mais de uma
  intenção ("quanto fica o C40 no PIX?" é produto e política);
- treinado com `datasets/intencoes.csv` (frases rotuladas à mão) quando o
  processo sobe, em menos de um segundo. Não há arquivo de modelo versionado.

A versão por palavras-chave continua aqui como base de comparação
(scripts/avaliar.py) e como reserva se o conjunto de dados não existir.
"""

import csv
import logging
import re
from dataclasses import dataclass
from functools import lru_cache

from . import config
from .data import normalize

log = logging.getLogger(__name__)

LABELS = ["produto", "pedido", "politica", "reclamacao", "saudacao", "fora_escopo"]
THRESHOLD = 0.4
DATASET = config.ROOT_DIR / "datasets" / "intencoes.csv"

KEYWORDS = {
    "pedido": "pedido compra comprei rastreio rastrear chegou chegar entregue encomenda nota fiscal",
    "politica": (
        "troca trocar devolver devolucao arrependi arrependimento garantia defeito frete entrega prazo pagamento "
        "pagar parcelar parcela parcelas vezes pix cartao boleto horario abre abrem fecha domingo sabado feriado "
        "endereco fica telefone whatsapp email contato promocao promocoes desconto cupom lgpd dados privacidade "
        "acessorio acessorios corda cordas palheta cabo amplificador pedal reembolso"
    ),
    "produto": (
        "violao violoes guitarra guitarras baixo baixos bateria baterias teclado teclados piano ukulele ukuleles "
        "sopro flauta saxofone violino violoncelo instrumento instrumentos preco precos custa estoque modelo "
        "modelos marca yamaha takamine fender tagima giannini roland casio"
    ),
    "reclamacao": "absurdo pessimo insatisfeito reclamacao procon gerente atendente humano pessoa chamado quebrou defeito",
    "saudacao": "oi ola bom dia boa tarde noite obrigado obrigada valeu tchau",
}
_KEYWORD_STEMS = {label: {w[:5] for w in normalize(words).split()} for label, words in KEYWORDS.items()}


def keyword_intents(text: str) -> list[str]:
    """Base de comparação: intenção por radicais de palavras-chave."""
    words = {w[:5] for w in normalize(text).split()}
    intents = [label for label, stems in _KEYWORD_STEMS.items() if label != "saudacao" and words & stems]
    if re.search(r"pedido\s*(n[ºo°.]?\s*)?\d+|#\d+", normalize(text)):
        intents.append("pedido")
    if not intents:
        intents = ["saudacao"] if words & _KEYWORD_STEMS["saudacao"] else ["fora_escopo"]
    return sorted(set(intents))


def load_dataset(path=DATASET) -> tuple[list[str], list[list[str]]]:
    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return [r["texto"] for r in rows], [r["rotulos"].split("|") for r in rows]


def build_pipeline():
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.multiclass import OneVsRestClassifier
    from sklearn.pipeline import FeatureUnion, Pipeline

    features = FeatureUnion(
        [
            ("chars", TfidfVectorizer(preprocessor=normalize, analyzer="char_wb", ngram_range=(2, 5), sublinear_tf=True)),
            ("words", TfidfVectorizer(preprocessor=normalize, ngram_range=(1, 2), sublinear_tf=True)),
        ]
    )
    model = OneVsRestClassifier(LogisticRegression(C=8.0, class_weight="balanced", max_iter=2000))
    return Pipeline([("features", features), ("model", model)])


@dataclass
class Prediction:
    labels: list[str]
    scores: dict[str, float]
    method: str  # "ml" ou "palavras-chave"


class IntentClassifier:
    def __init__(self, texts: list[str], labels: list[list[str]]):
        from sklearn.preprocessing import MultiLabelBinarizer

        self.binarizer = MultiLabelBinarizer(classes=LABELS)
        self.pipeline = build_pipeline().fit(texts, self.binarizer.fit_transform(labels))

    def predict(self, text: str) -> Prediction:
        probs = self.pipeline.predict_proba([text])[0]
        scores = {label: round(float(p), 3) for label, p in zip(LABELS, probs)}
        labels = [label for label, p in scores.items() if p >= THRESHOLD]
        if not labels:  # nenhuma passou do limiar: fica a mais provável
            labels = [max(scores, key=scores.get)]
        return Prediction(labels, scores, "ml")


@lru_cache(maxsize=1)
def load_classifier() -> IntentClassifier | None:
    try:
        return IntentClassifier(*load_dataset())
    except Exception as exc:  # noqa: BLE001 — sem dados ou sem sklearn: usa palavras-chave
        log.warning("Classificador de intenção indisponível (%s); usando palavras-chave.", exc)
        return None


def predict(text: str) -> Prediction:
    classifier = load_classifier()
    if classifier is None:
        return Prediction(keyword_intents(text), {}, "palavras-chave")
    return classifier.predict(text)
