"""Camada de dados: carrega os CSVs e aplica as regras de negócio de forma determinística.

Tudo que envolve número (preço com desconto, parcelamento, prazos de troca) é
calculado aqui, em código, e não pelo LLM.
"""

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import cached_property
from pathlib import Path

import pandas as pd

from . import config

PIX_DISCOUNT = 0.05
# (máximo de parcelas, parcela mínima) — seção 3.1 do manual, conforme notas de curadoria.
INSTALLMENT_RULES = [(3, 50.0), (6, 80.0), (12, 100.0)]

# Seção 2 do manual. weekday(): segunda = 0 ... domingo = 6.
OPENING_HOURS = {d: (time(9), time(18)) for d in range(5)} | {5: (time(9), time(13))}

ORDER_STATUS_PT = {
    "pending": "aguardando confirmação de pagamento",
    "confirmed": "pagamento confirmado, em separação",
    "shipped": "enviado",
    "delivered": "entregue",
    "cancelled": "cancelado",
}

PAYMENT_PT = {"pix": "PIX", "debit": "cartão de débito", "boleto": "boleto bancário"}


def brl(value: float) -> str:
    """Formata no padrão brasileiro: 1234.5 -> 'R$ 1.234,50'."""
    return "R$ " + f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def normalize(text: str) -> str:
    """Minúsculas e sem acentos, para busca tolerante ('violao' casa com 'Violão')."""
    text = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def digits(text: str) -> str:
    return re.sub(r"\D", "", str(text))


def payment_method_pt(code: str) -> str:
    if code.startswith("credit_"):
        return f"cartão de crédito em {code.removeprefix('credit_')}"
    return PAYMENT_PT.get(code, code)


def payment_conditions(list_price: float, promo_price: float | None) -> dict:
    """Preço à vista no PIX e parcelamento máximo sem juros para um valor."""
    price = promo_price if promo_price is not None else list_price
    max_installments = 1
    for limit, min_installment in INSTALLMENT_RULES:
        for n in range(limit, max_installments, -1):
            if price / n >= min_installment:
                max_installments = n
                break
    conditions = {
        "parcelamento_max_sem_juros": max_installments,
        "valor_parcela": brl(price / max_installments),
    }
    if promo_price is None:
        conditions["preco_pix"] = brl(list_price * (1 - PIX_DISCOUNT))
    else:
        conditions["preco_pix"] = brl(promo_price)
        conditions["obs_pix"] = "Desconto PIX de 5% não acumula com promoção."
    return conditions


def store_status(moment: datetime) -> dict:
    """Se a loja está aberta agora e, se não, quando reabre. Feriados não são tratados."""
    hours = OPENING_HOURS.get(moment.weekday())
    if hours and hours[0] <= moment.time() < hours[1]:
        return {"aberta_agora": True, "fecha_as": hours[1].strftime("%H:%M")}
    for offset in range(0, 8):
        day = moment.date() + timedelta(days=offset)
        hours = OPENING_HOURS.get(day.weekday())
        if hours and (offset > 0 or moment.time() < hours[0]):
            return {"aberta_agora": False, "reabre": f"{day:%d/%m} às {hours[0]:%H:%M}"}
    return {"aberta_agora": False}


@dataclass
class Store:
    data_dir: Path = config.DATA_DIR

    def _read(self, table: str) -> pd.DataFrame:
        return pd.read_csv(self.data_dir / f"desafio_tecnico_ai_eng - {table}.csv")

    @cached_property
    def categories(self) -> pd.DataFrame:
        return self._read("categories")

    @cached_property
    def products(self) -> pd.DataFrame:
        df = self._read("products").merge(
            self.categories[["category_id", "name"]].rename(columns={"name": "category"}),
            on="category_id",
            how="left",
        )
        df["specs"] = df["specs"].apply(lambda s: json.loads(s) if isinstance(s, str) else {})
        df["_name_norm"] = df["name"].map(normalize)
        df["_text_norm"] = (df["name"] + " " + df["category"] + " " + df["description"]).map(normalize)
        return df

    @cached_property
    def active_promotions(self) -> pd.DataFrame:
        """Só promoções ativas; se houver mais de uma por produto, vale a de maior desconto."""
        promos = self._read("promotions")
        promos = promos[promos["is_active"] == 1]
        return promos.sort_values("discount_percent", ascending=False).drop_duplicates("product_id")

    @cached_property
    def orders(self) -> pd.DataFrame:
        return self._read("orders").fillna({"tracking_code": "", "notes": ""})

    @cached_property
    def order_items(self) -> pd.DataFrame:
        return self._read("order_items")

    @cached_property
    def customers(self) -> pd.DataFrame:
        return self._read("customers")

    # ---------- catálogo ----------

    def list_categories(self) -> list[dict]:
        sellable = self.products[self.products["status"] == "active"]
        counts = sellable.groupby("category_id").size()
        return [
            {
                "categoria": row.name,
                "descricao": row.description,
                "produtos_no_catalogo": int(counts.get(row.category_id, 0)),
            }
            for row in self.categories.itertuples()
        ]

    def search_products(
        self,
        query: str | None = None,
        category: str | None = None,
        min_price: float | None = None,
        max_price: float | None = None,
        include_unavailable: bool = False,
        limit: int = 10,
    ) -> dict:
        df = self.products
        if not include_unavailable:
            df = df[(df["status"] == "active") & (df["stock_quantity"] > 0)]
        if category:
            df = df[df["category"].map(normalize).str.contains(normalize(category), regex=False)]

        df = df.copy()
        df["_price"] = [self._promo_price(r)[0] for r in df.itertuples()]
        if min_price is not None:
            df = df[df["_price"] >= min_price]
        if max_price is not None:
            df = df[df["_price"] <= max_price]

        if query:
            tokens = normalize(query).split()
            # Nome pesa mais que descrição; exige ao menos um termo encontrado.
            df["_score"] = [
                sum(2 if t in name else 1 if t in text else 0 for t in tokens)
                for name, text in zip(df["_name_norm"], df["_text_norm"])
            ]
            df = df[df["_score"] > 0].sort_values(["_score", "_price"], ascending=[False, True])
        else:
            df = df.sort_values("_price")

        return {
            "total_encontrado": len(df),
            "produtos": [self._product_summary(r) for r in df.head(limit).itertuples()],
        }

    def get_product(self, product_id: int) -> dict | None:
        match = self.products[self.products["product_id"] == product_id]
        if match.empty:
            return None
        row = next(match.itertuples())
        details = self._product_summary(row)
        details["descricao"] = row.description
        details["especificacoes"] = row.specs
        return details

    def _promo_price(self, row) -> tuple[float, float | None]:
        """(preço efetivo, preço promocional ou None)."""
        promo = self.active_promotions[self.active_promotions["product_id"] == row.product_id]
        if promo.empty:
            return row.price_brl, None
        discounted = round(row.price_brl * (1 - promo.iloc[0]["discount_percent"] / 100), 2)
        return discounted, discounted

    def _product_summary(self, row) -> dict:
        summary = {
            "produto_id": int(row.product_id),
            "nome": row.name,
            "categoria": row.category,
            "preco": brl(row.price_brl),
            "disponibilidade": self._availability(row),
        }
        promo = self.active_promotions[self.active_promotions["product_id"] == row.product_id]
        if not promo.empty:
            p = promo.iloc[0]
            summary["promocao"] = {
                "nome": p["description"],
                "desconto_percentual": int(p["discount_percent"]),
                "preco_original": brl(row.price_brl),
                "preco_promocional": brl(self._promo_price(row)[0]),
            }
        # Já vem na busca para o modelo não precisar de uma chamada extra por produto.
        summary["condicoes_pagamento"] = payment_conditions(row.price_brl, self._promo_price(row)[1])
        return summary

    @staticmethod
    def _availability(row) -> str:
        if row.status == "discontinued":
            return "descontinuado (fora do catálogo)"
        if row.status == "coming_soon":
            return "lançamento em breve (ainda não disponível para venda)"
        if row.stock_quantity <= 0:
            return "temporariamente sem estoque"
        if row.stock_quantity <= 2:
            return "disponível (últimas unidades)"
        return "disponível"

    # ---------- pedidos ----------

    def get_order(self, order_id: int, contact: str, today: date) -> dict:
        """Pedido + itens, liberado só se o e-mail ou telefone bater com o do cliente (LGPD)."""
        match = self.orders[self.orders["order_id"] == order_id]
        if match.empty:
            return {"erro": f"Pedido {order_id} não encontrado."}
        order = match.iloc[0]
        customer = self.customers[self.customers["customer_id"] == order["customer_id"]].iloc[0]

        contact = contact.strip()
        email_ok = contact.lower() == str(customer["email"]).lower()
        phone_ok = len(digits(contact)) >= 8 and digits(customer["phone"]).endswith(digits(contact)[-8:])
        if not (email_ok or phone_ok):
            return {
                "erro": "Os dados de verificação não conferem com o cadastro deste pedido. "
                "Não revele nenhuma informação do pedido."
            }

        items = self.order_items[self.order_items["order_id"] == order_id].merge(
            self.products[["product_id", "name"]], on="product_id"
        )
        result = {
            "pedido": int(order_id),
            "cliente_primeiro_nome": customer["name"].split()[0],
            "cidade_entrega": customer["city"],
            "data_compra": _fmt_date(order["order_date"]),
            "status": ORDER_STATUS_PT.get(order["status"], order["status"]),
            "valor_total": brl(order["total_brl"]),
            "forma_pagamento": payment_method_pt(order["payment_method"]),
            "itens": [{"produto": r.name, "quantidade": int(r.quantity)} for r in items.itertuples()],
        }
        if order["tracking_code"]:
            result["codigo_rastreio"] = order["tracking_code"]
        if isinstance(order["estimated_delivery"], str):
            result["previsao_entrega"] = _fmt_date(order["estimated_delivery"])
        if order["notes"]:
            result["observacao"] = order["notes"]
        if order["status"] == "delivered":
            result["prazos_pos_venda"] = _after_sale_deadlines(order, today)
        return result


def _fmt_date(iso: str) -> str:
    return date.fromisoformat(iso).strftime("%d/%m/%Y")


def _after_sale_deadlines(order: pd.Series, today: date) -> dict:
    """Prazos das seções 4 e 8 do manual.

    Os dados não têm data real de entrega; usamos a previsão de entrega como
    aproximação (premissa documentada no README).
    """
    purchased = date.fromisoformat(order["order_date"])
    received = date.fromisoformat(order["estimated_delivery"])
    days_since_receipt = (today - received).days
    days_since_purchase = (today - purchased).days
    return {
        "data_recebimento_considerada": received.strftime("%d/%m/%Y") + " (previsão de entrega)",
        "dias_desde_recebimento": days_since_receipt,
        "arrependimento_7_dias_ok": days_since_receipt <= 7,
        "troca_por_preferencia_7_dias_ok": days_since_receipt <= 7,
        "troca_por_defeito_30_dias_ok": days_since_purchase <= 30,
        "garantia_legal_90_dias_ok": days_since_receipt <= 90,
    }
