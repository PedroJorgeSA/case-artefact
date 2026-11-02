"""Testes das regras determinísticas (sem chamar o LLM)."""

import json
from datetime import date, datetime

import pytest

from emporio_agent import config
from emporio_agent.data import Store, brl, payment_conditions, store_status
from emporio_agent.tools import ToolExecutor


@pytest.fixture(scope="module")
def store():
    return Store()


def test_brl():
    assert brl(1234.5) == "R$ 1.234,50"


def test_pix_discount_only_without_promo():
    assert payment_conditions(1000, None)["preco_pix"] == "R$ 950,00"
    assert payment_conditions(1000, 900)["preco_pix"] == "R$ 900,00"


@pytest.mark.parametrize(
    "price, expected",
    [(40, 1), (100, 2), (159.9, 3), (349.9, 4), (599, 6), (700, 7), (2199, 12)],
)
def test_installments_respect_minimums(price, expected):
    assert payment_conditions(price, None)["parcelamento_max_sem_juros"] == expected


def test_store_hours():
    assert store_status(datetime(2026, 3, 25, 10, 0, tzinfo=config.STORE_TZ))["aberta_agora"]  # quarta
    sunday = store_status(datetime(2026, 3, 29, 10, 0, tzinfo=config.STORE_TZ))
    assert not sunday["aberta_agora"] and sunday["reabre"] == "30/03 às 09:00"
    saturday_afternoon = store_status(datetime(2026, 3, 28, 14, 0, tzinfo=config.STORE_TZ))
    assert saturday_afternoon["reabre"] == "30/03 às 09:00"


def test_search_by_model_name(store):
    result = store.search_products("Takamine GD20")
    assert result["produtos"][0]["nome"] == "Takamine GD20 Dreadnought Natural"
    assert "promocao" not in result["produtos"][0]  # promoção 4 está inativa
    assert "preco_pix" in result["produtos"][0]["condicoes_pagamento"]  # já vem na busca


def test_search_excludes_out_of_stock(store):
    names = [p["nome"] for p in store.search_products(category="violoes", max_price=1000, limit=50)["produtos"]]
    assert "Giannini GF-3D Dreadnought Sunburst" not in names  # estoque 0
    assert len(names) > 10


def test_active_promotion_shows_original_and_discount(store):
    promo = store.get_product(90)["promocao"]
    assert promo == {
        "nome": "Desconto Exclusivo",
        "desconto_percentual": 18,
        "preco_original": "R$ 2.399,00",
        "preco_promocional": "R$ 1.967,18",
    }


def test_empty_category_has_no_products(store):
    sopro = [c for c in store.list_categories() if "Sopro" in c["categoria"]]
    assert all(c["produtos_no_catalogo"] == 0 for c in sopro)


def test_order_requires_matching_contact(store):
    denied = store.get_order(1, "alguem@exemplo.com", date(2026, 3, 25))
    assert "erro" in denied and "pedido" not in denied
    ok = store.get_order(1, "pedro.oliveira@jmail.com", date(2026, 3, 25))
    assert ok["status"] == "entregue" and ok["codigo_rastreio"] == "BRAB1234567BR"
    by_phone = store.get_order(1, "(67) 98432-1098", date(2026, 3, 25))
    assert by_phone["pedido"] == 1


def test_order_denial_does_not_reveal_if_order_exists(store):
    wrong_contact = store.get_order(1, "alguem@exemplo.com", date(2026, 3, 25))
    missing_order = store.get_order(999, "alguem@exemplo.com", date(2026, 3, 25))
    assert wrong_contact == missing_order
    assert store.get_order(1, None, date(2026, 3, 25)) == wrong_contact


def test_return_window(store):
    # Pedido 7: previsão de entrega 17/02/2026.
    within = store.get_order(7, "leticia.rocha@jmail.com", date(2026, 2, 20))["prazos_pos_venda"]
    assert within["arrependimento_7_dias_ok"]
    late = store.get_order(7, "leticia.rocha@jmail.com", date(2026, 3, 25))["prazos_pos_venda"]
    assert not late["arrependimento_7_dias_ok"] and late["garantia_legal_90_dias_ok"]
    assert late["dias_desde_recebimento"] == 36  # previsão de entrega 17/02 -> 25/03


def test_executor_reports_unknown_tool():
    output, is_error = ToolExecutor().run("apagar_tudo", {})
    assert is_error and "desconhecida" in json.loads(output)["erro"]


def test_search_only_promotions(store):
    result = store.search_products(category="Violões", only_promotions=True)
    names = [p["nome"] for p in result["produtos"]]
    assert result["total_encontrado"] == 2
    assert all(p["promocao"] for p in result["produtos"])
    assert any("Crafter" in n for n in names) and any("Taylor" in n for n in names)
