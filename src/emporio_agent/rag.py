"""RAG híbrido sobre o manual de políticas: BM25 + embeddings, fundidos por RRF.

Por que RAG num manual de ~2 mil tokens: o plano gratuito do Groq limita cada
chamada a 8 mil tokens. Mandar só os 3 trechos relevantes em vez do manual
inteiro libera ~1.500 tokens por chamada para histórico e resultados de
ferramentas, e cada resposta passa a citar a seção exata ([§4.1]).

Decisões:
- Um trecho por seção do manual (## e ###), com o caminho da seção no começo
  do texto ("4. Trocas › 4.1 Direito de arrependimento"). É a ideia do
  "contextual retrieval" sem precisar de LLM: o trecho carrega o próprio contexto.
- BM25 escrito aqui (poucas linhas), com normalização para português: sem
  acentos, sem stopwords, radical por prefixo de 5 letras ("devolução" e
  "devolver" viram "devol") e expansão de sinônimos do cliente para o
  vocabulário do manual ("abre domingo" -> "horário funcionamento").
- Embeddings multilíngues locais (fastembed/ONNX, sem torch). Se o modelo não
  puder ser baixado, ou EMPORIO_EMBEDDINGS=off, a busca segue só com BM25.
- Reciprocal Rank Fusion (k=60) junta os dois rankings sem calibrar escalas.
"""

import hashlib
import logging
import math
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache

from . import config
from .data import normalize

log = logging.getLogger(__name__)

EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
RRF_K = 60
TOP_K = 3
STEM_LEN = 5
# Abaixo disso, um trecho sem nenhuma palavra em comum com a pergunta é descartado.
MIN_DENSE_SIM = 0.30
# Seções que não vão para o índice: a 7 (diretrizes de atendimento) é instrução
# para a Lia e vai inteira no prompt; as notas de curadoria são internas.
SKIP_SECTIONS = ("7", "notas-de-curadoria")

STOPWORDS = set(
    normalize(
        "a o as os um uma uns umas de do da dos das no na nos nas em por para pra pro com sem "
        "e ou que se me te nos vos lhe eu tu ele ela voces vocês voce você meu minha seu sua "
        "isso isto esse essa este esta aquele aquela qual quais quando como onde ate até "
        "mais menos muito pouco ja já tem ter tenho tá ta é foi ser sao são estou está esta "
        "posso pode podem queria quero gostaria saber sobre oi ola olá bom dia boa tarde noite obrigado obrigada"
    ).split()
)

# Palavras do cliente -> vocabulário do manual. Chaves e valores passam pela
# mesma normalização, então basta o radical bater.
SYNONYMS = {
    "abre abrem aberto aberta fecha fecham fechado funciona horas domingo sabado feriado expediente":
        "horario funcionamento",
    "devolver devolvo arrependi arrependimento desistir desisti cancelar reembolso estorno": "arrependimento devolucao reembolso",
    "trocar troco troca": "troca",
    "quebrou quebrado estragou estragado defeito defeituoso empenando empenado problema funcionando": "defeito garantia",
    "entrega entregar chega chegar demora envio enviar frete correios sedex pac jadlog motoboy": "frete entrega prazo",
    "parcelar parcela parcelas vezes cartao credito debito pix boleto pagar pagamento": "pagamento parcelamento",
    "desconto promocao promocoes cupom liquidacao black friday": "promocao desconto",
    "endereco fica localizacao telefone whatsapp email contato cnpj loja": "endereco telefone dados empresa",
    "dados lgpd privacidade excluir apagar": "privacidade dados pessoais",
    "rastreio rastrear rastreamento codigo": "rastreamento codigo",
    "acessorio acessorios cordas palheta palhetas cabo cabos case pedal amplificador": "acessorios exclusivamente instrumentos",
}


def _stem(token: str) -> str:
    return token[:STEM_LEN]


def tokenize(text: str) -> list[str]:
    return [_stem(t) for t in normalize(text).split() if len(t) > 1 and t not in STOPWORDS]


_SYNONYM_INDEX: dict[str, list[str]] = {}
for _words, _expansion in SYNONYMS.items():
    for _word in normalize(_words).split():
        _SYNONYM_INDEX.setdefault(_stem(_word), []).extend(tokenize(_expansion))


def expand_query(tokens: list[str]) -> list[str]:
    """Acrescenta os termos do manual ligados às palavras do cliente."""
    extra = [t for token in tokens for t in _SYNONYM_INDEX.get(token, [])]
    return tokens + extra


@dataclass
class Chunk:
    id: str  # número da seção ("4.1") ou slug
    path: str  # "4. Trocas e devoluções › 4.1 Direito de arrependimento"
    body: str

    @property
    def text(self) -> str:
        """Texto indexado e enviado ao modelo: o caminho da seção vem junto."""
        return f"{self.path}\n{self.body}"


@dataclass
class Hit:
    chunk: Chunk
    score: float  # RRF
    bm25: float
    dense: float | None
    rank_bm25: int | None
    rank_dense: int | None

    def as_dict(self) -> dict:
        return {
            "id": self.chunk.id,
            "secao": self.chunk.path,
            "texto": self.chunk.body,
            "score": round(self.score, 4),
            "bm25": round(self.bm25, 3),
            "similaridade": None if self.dense is None else round(self.dense, 3),
            "rank_bm25": self.rank_bm25,
            "rank_denso": self.rank_dense,
        }


@dataclass
class Retrieval:
    query: str
    hits: list[Hit]
    mode: str  # "hibrido" ou "bm25"
    expanded_terms: list[str] = field(default_factory=list)

    @property
    def confidence(self) -> str:
        """'alta' se algum trecho casa por palavra e por sentido; 'nenhuma' se nada passou do filtro."""
        if not self.hits:
            return "nenhuma"
        best = self.hits[0]
        if best.bm25 > 0 and (best.dense is None or best.dense >= 0.4):
            return "alta"
        return "media"

    def as_dict(self) -> dict:
        return {
            "consulta": self.query,
            "modo": self.mode,
            "confianca": self.confidence,
            "termos": self.expanded_terms,
            "trechos": [h.as_dict() for h in self.hits],
        }

    def as_context(self) -> str:
        """Bloco que vai para o modelo, cada trecho com o id para citação."""
        if not self.hits:
            return "Nenhum trecho do manual corresponde a esta pergunta."
        return "\n\n".join(f"[§{h.chunk.id}] {h.chunk.text}" for h in self.hits)


def _slug(text: str) -> str:
    return normalize(text).replace(" ", "-")


def chunk_markdown(markdown: str) -> list[Chunk]:
    """Um trecho por seção ## ou ###. O texto de uma ## antes da primeira ### vira trecho próprio."""
    chunks: list[Chunk] = []
    h2 = h3 = None
    body: list[str] = []

    def flush():
        text = "\n".join(body).strip()
        heading = h3 or h2
        if heading and text:
            number = re.match(r"(\d+(?:\.\d+)?)\.?\s", heading)
            chunk_id = number.group(1) if number else _slug(heading)
            path = f"{h2} › {h3}" if h3 else h2
            chunks.append(Chunk(chunk_id, path, text))
        body.clear()

    for line in markdown.splitlines():
        if line.startswith("## "):
            flush()
            h2, h3 = line[3:].strip(), None
        elif line.startswith("### "):
            flush()
            h3 = line[4:].strip()
        elif line.startswith("# ") or line.startswith(">") or line.strip() == "---":
            continue
        else:
            body.append(line)
    flush()

    def skipped(chunk: Chunk) -> bool:
        top = chunk.id.split(".")[0] if chunk.id[0].isdigit() else chunk.id
        return top in SKIP_SECTIONS or any(chunk.id.startswith(s) for s in SKIP_SECTIONS if not s[0].isdigit())

    # "Dados da empresa" é subseção sem número: ganha o id da seção pai.
    for chunk in chunks:
        if not chunk.id[0].isdigit() and chunk.path.startswith("1."):
            chunk.id = "1.1"
    return [c for c in chunks if not skipped(c)]


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.docs = docs
        self.k1, self.b = k1, b
        self.avgdl = sum(map(len, docs)) / len(docs)
        self.tf = [Counter(d) for d in docs]
        df = Counter(t for d in docs for t in set(d))
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def scores(self, query: list[str]) -> list[float]:
        out = []
        for tf, doc in zip(self.tf, self.docs):
            norm = self.k1 * (1 - self.b + self.b * len(doc) / self.avgdl)
            out.append(sum(self.idf.get(t, 0) * tf[t] * (self.k1 + 1) / (tf[t] + norm) for t in query if t in tf))
        return out


class DenseIndex:
    """Embeddings dos trechos, com cache em disco pelo hash do conteúdo."""

    def __init__(self, chunks: list[Chunk]):
        import numpy as np
        from fastembed import TextEmbedding

        self.np = np
        self.model = TextEmbedding(EMBEDDING_MODEL, cache_dir=str(config.CACHE_DIR / "fastembed"))
        texts = [c.text for c in chunks]
        digest = hashlib.sha256(("\n\n".join(texts) + EMBEDDING_MODEL).encode()).hexdigest()[:16]
        cache = config.CACHE_DIR / f"politicas-{digest}.npy"
        if cache.exists():
            matrix = np.load(cache)
        else:
            matrix = np.array(list(self.model.embed(texts)), dtype="float32")
            config.CACHE_DIR.mkdir(exist_ok=True)
            np.save(cache, matrix)
        self.matrix = matrix / np.linalg.norm(matrix, axis=1, keepdims=True)

    def similarities(self, query: str) -> list[float]:
        q = self.np.array(next(iter(self.model.query_embed([query]))), dtype="float32")
        return (self.matrix @ (q / self.np.linalg.norm(q))).tolist()


def _ranks(scores: list[float], keep: list[bool]) -> dict[int, int]:
    order = sorted((i for i in range(len(scores)) if keep[i]), key=lambda i: -scores[i])
    return {i: r for r, i in enumerate(order, start=1)}


class PolicyIndex:
    def __init__(self, chunks: list[Chunk], use_embeddings: bool | None = None):
        self.chunks = chunks
        self.bm25 = BM25([tokenize(c.text) for c in chunks])
        if use_embeddings is None:
            use_embeddings = os.getenv("EMPORIO_EMBEDDINGS", "on").lower() not in ("off", "0", "false")
        self.dense: DenseIndex | None = None
        if use_embeddings:
            try:
                self.dense = DenseIndex(chunks)
            except Exception as exc:  # noqa: BLE001 — sem rede ou sem modelo: segue com BM25
                log.warning("Embeddings indisponíveis (%s); usando só BM25.", exc)

    @property
    def mode(self) -> str:
        return "hibrido" if self.dense else "bm25"

    def get(self, chunk_id: str) -> Chunk | None:
        return next((c for c in self.chunks if c.id == chunk_id), None)

    def search(self, query: str, k: int = TOP_K) -> Retrieval:
        base = tokenize(query)
        terms = expand_query(base)
        bm25 = self.bm25.scores(terms)
        dense = self.dense.similarities(query) if self.dense else None

        # Um trecho só concorre se tem alguma palavra em comum ou é próximo no sentido.
        keep = [bm25[i] > 0 or (dense is not None and dense[i] >= MIN_DENSE_SIM) for i in range(len(self.chunks))]
        rank_bm25 = _ranks(bm25, [keep[i] and bm25[i] > 0 for i in range(len(keep))])
        rank_dense = _ranks(dense, keep) if dense else {}
        hits = []
        for i, chunk in enumerate(self.chunks):
            if not keep[i]:
                continue
            score = sum(1 / (RRF_K + r[i]) for r in (rank_bm25, rank_dense) if i in r)
            hits.append(Hit(chunk, score, bm25[i], dense[i] if dense else None, rank_bm25.get(i), rank_dense.get(i)))
        hits.sort(key=lambda h: -h.score)
        return Retrieval(query, hits[:k], self.mode, sorted(set(terms) - set(base)))


@lru_cache(maxsize=2)
def load_index(use_embeddings: bool | None = None) -> PolicyIndex:
    markdown = (config.KNOWLEDGE_DIR / "politicas.md").read_text(encoding="utf-8")
    return PolicyIndex(chunk_markdown(markdown), use_embeddings)


def guidelines_section() -> str:
    """Seção 7 do manual (diretrizes de atendimento), que vai inteira no prompt."""
    markdown = (config.KNOWLEDGE_DIR / "politicas.md").read_text(encoding="utf-8")
    match = re.search(r"^## 7\..*?(?=^## )", markdown, flags=re.S | re.M)
    return match.group(0).strip() if match else ""
