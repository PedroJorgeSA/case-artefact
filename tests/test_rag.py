"""Testes da busca no manual (só BM25, para rodar sem baixar o modelo de embeddings)."""

import csv

import pytest

from emporio_agent import config
from emporio_agent.rag import chunk_markdown, expand_query, load_index, tokenize


@pytest.fixture(scope="module")
def index():
    return load_index(False)


def test_one_chunk_per_section_without_internal_ones(index):
    ids = [c.id for c in index.chunks]
    assert {"1.1", "2", "3.1", "4.1", "4.2", "5.3", "8", "9"} <= set(ids)
    assert not any(i.startswith("7") for i in ids)  # diretrizes vão no prompt
    assert not any("curadoria" in c.path.lower() for c in index.chunks)


def test_chunk_text_carries_the_section_path(index):
    chunk = index.get("4.1")
    assert chunk.text.startswith("4. Trocas e devoluções › 4.1 Direito de arrependimento")


def test_chunker_handles_intro_text_and_subsections():
    chunks = chunk_markdown("# T\n\n## 1. A\ntexto a\n\n### 1.1 B\ntexto b\n## 2. C\ntexto c\n")
    assert [(c.id, c.body) for c in chunks] == [("1", "texto a"), ("1.1", "texto b"), ("2", "texto c")]


def test_portuguese_normalization_matches_word_families():
    assert tokenize("devolução") == tokenize("devolver")
    assert "horar" in expand_query(tokenize("vocês abrem domingo?"))


def test_golden_questions_hit_top3(index):
    with open(config.ROOT_DIR / "datasets" / "perguntas_manual.csv", encoding="utf-8") as f:
        golden = list(csv.DictReader(f))
    hits = sum(
        bool(set(row["secoes_esperadas"].split("|")) & {h.chunk.id for h in index.search(row["pergunta"]).hits})
        for row in golden
    )
    assert hits / len(golden) >= 0.9


def test_out_of_scope_question_retrieves_nothing(index):
    result = index.search("qual a capital da França?")
    assert result.hits == [] and result.confidence == "nenhuma"
    assert "Nenhum trecho" in result.as_context()
