"""Ferramentas expostas ao LLM (function calling) e o despacho para a camada de dados.

Escolha deliberada: ferramentas estreitas e tipadas em vez de text-to-SQL. O
modelo só enxerga o que cada ferramenta devolve, então dados de outros clientes
nunca chegam ao contexto.
"""

import json
import uuid
from datetime import datetime

from . import config
from .data import Store

NULLABLE_STR = {"type": ["string", "null"]}
NULLABLE_NUM = {"type": ["number", "null"]}


def _schema(properties: dict) -> dict:
    # strict: true exige additionalProperties false e todos os campos em required;
    # campos opcionais viram tipos que aceitam null.
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


TOOLS = [
    {
        "name": "buscar_produtos",
        "description": (
            "Busca instrumentos no catálogo da loja, com preço, promoção ativa e disponibilidade. "
            "Use sempre que o cliente perguntar sobre produtos, preços, modelos, marcas ou estoque. "
            "Por padrão retorna só itens disponíveis para venda."
        ),
        "input_schema": _schema(
            {
                "termo": {
                    **NULLABLE_STR,
                    "description": "Palavras-chave como marca, modelo ou tipo (ex.: 'Takamine GD20', 'nylon', 'elétrico').",
                },
                "categoria": {
                    **NULLABLE_STR,
                    "description": "Nome da categoria (ex.: 'Violões', 'Ukuleles', 'Guitarras').",
                },
                "preco_min": {**NULLABLE_NUM, "description": "Preço mínimo em reais."},
                "preco_max": {**NULLABLE_NUM, "description": "Preço máximo em reais."},
                "incluir_indisponiveis": {
                    "type": "boolean",
                    "description": "true para incluir itens sem estoque, descontinuados ou em lançamento.",
                },
            }
        ),
        "strict": True,
    },
    {
        "name": "detalhes_produto",
        "description": (
            "Detalhes de um produto pelo ID: especificações técnicas, descrição, promoção ativa e "
            "condições de pagamento já calculadas (preço no PIX, parcelamento máximo sem juros)."
        ),
        "input_schema": _schema({"produto_id": {"type": "integer", "description": "ID do produto."}}),
        "strict": True,
    },
    {
        "name": "listar_categorias",
        "description": "Lista as categorias da loja e quantos produtos cada uma tem à venda.",
        "input_schema": _schema({}),
        "strict": True,
    },
    {
        "name": "consultar_pedido",
        "description": (
            "Consulta status, itens, rastreio e prazos de troca/devolução de um pedido. "
            "Exige o número do pedido E o e-mail ou telefone cadastrado, que o cliente deve informar. "
            "Nunca invente nem suponha esses dados."
        ),
        "input_schema": _schema(
            {
                "numero_pedido": {"type": "integer", "description": "Número do pedido."},
                "email_ou_telefone": {
                    "type": "string",
                    "description": "E-mail ou telefone informado pelo cliente para confirmar a identidade.",
                },
            }
        ),
        "strict": True,
    },
    {
        "name": "encaminhar_para_atendente",
        "description": (
            "Registra um chamado para a equipe humana. Use em reclamações, pedidos de troca, "
            "devolução ou garantia, dúvidas de política que o manual não resolve, ou quando o "
            "cliente pedir para falar com uma pessoa."
        ),
        "input_schema": _schema(
            {
                "motivo": {
                    "type": "string",
                    "enum": ["reclamacao", "troca_devolucao", "garantia", "duvida_politica", "outro"],
                },
                "resumo": {"type": "string", "description": "Resumo objetivo do caso para a equipe."},
                "contato_cliente": {
                    **NULLABLE_STR,
                    "description": "Nome e telefone/e-mail para retorno, se o cliente informou.",
                },
                "numero_pedido": {"type": ["integer", "null"]},
            }
        ),
        "strict": True,
    },
]


class ToolExecutor:
    def __init__(self, store: Store | None = None):
        self.store = store or Store()

    def run(self, name: str, args: dict) -> tuple[str, bool]:
        """Executa a ferramenta e devolve (JSON do resultado, is_error)."""
        handler = getattr(self, f"_{name}", None)
        if handler is None:
            return json.dumps({"erro": f"Ferramenta desconhecida: {name}"}), True
        try:
            result = handler(**args)
        except Exception as exc:  # noqa: BLE001 — o erro volta para o modelo tratar
            return json.dumps({"erro": f"{type(exc).__name__}: {exc}"}), True
        return json.dumps(result, ensure_ascii=False, default=str), "erro" in result

    def _buscar_produtos(self, termo, categoria, preco_min, preco_max, incluir_indisponiveis):
        return self.store.search_products(termo, categoria, preco_min, preco_max, incluir_indisponiveis)

    def _detalhes_produto(self, produto_id):
        return self.store.get_product(produto_id) or {"erro": f"Produto {produto_id} não encontrado."}

    def _listar_categorias(self):
        return {"categorias": self.store.list_categories()}

    def _consultar_pedido(self, numero_pedido, email_ou_telefone):
        return self.store.get_order(numero_pedido, email_ou_telefone, config.now().date())

    def _encaminhar_para_atendente(self, motivo, resumo, contato_cliente, numero_pedido):
        ticket = {
            "protocolo": uuid.uuid4().hex[:8].upper(),
            "criado_em": datetime.now(config.STORE_TZ).isoformat(timespec="seconds"),
            "motivo": motivo,
            "resumo": resumo,
            "contato_cliente": contato_cliente,
            "numero_pedido": numero_pedido,
        }
        config.HANDOFFS_DIR.mkdir(exist_ok=True)
        with open(config.HANDOFFS_DIR / "chamados.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(ticket, ensure_ascii=False) + "\n")
        return {
            "protocolo": ticket["protocolo"],
            "prazo_retorno": "até 24 horas úteis",
        }
