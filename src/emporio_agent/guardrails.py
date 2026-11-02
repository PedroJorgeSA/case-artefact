"""Guardrails determinísticos, sem chamada extra ao modelo.

Cada chamada ao Groq gasta do mesmo limite de 8 mil tokens por minuto, então as
verificações aqui são regras de código: rápidas, testáveis e de graça.

- Entrada: detecta tentativa de mudar as regras (prompt injection) e remove
  dados que nunca deveriam passar pelo chat (cartão, CPF) antes de chegarem ao
  modelo ou ao histórico.
- Saída: confere se cada valor em reais, protocolo e seção citada na resposta
  aparece nas fontes do turno (ferramentas, trechos do manual, prompt). Se não
  aparece, o grafo pede uma nova versão ao modelo.
- Mascaramento: e-mails e telefones viram "l***@jmail.com" nos eventos da
  interface e nos registros.
"""

import json
import re
from dataclasses import dataclass, field

from .data import normalize

INJECTION_PATTERNS = [
    r"ignor[ea]\w* (todas |as |os |suas |tudo )*(as |os )?(instruc|regras|orientac|anteriores)",
    r"ignore (all |the |your )*(previous |prior )?(instructions|rules)",
    r"esquec[ea]\w* (todas |as |suas )*(instruc|regras)",
    r"(voce|vc) agora e\b",
    r"you are now\b",
    r"(finja|fingir|aja|age) como",
    r"system prompt|prompt do sistema|suas instrucoes|developer mode|modo desenvolvedor|jailbreak",
    r"(liste|listar|mostre|mostrar|me (de|passe)) .{0,30}(clientes|e-?mails|telefones|cpfs|pedidos de outr)",
]
_INJECTION_RE = [re.compile(p) for p in INJECTION_PATTERNS]

_CARD_RE = re.compile(r"\b(?:\d[ .-]?){13,19}\b")
_CPF_RE = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")
_EMAIL_RE = re.compile(r"\b([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
_PHONE_RE = re.compile(r"(\(?\d{2}\)?\s?)9?\d{4}[-\s]?(\d{4})\b")
_MONEY_RE = re.compile(r"R\$\s?(\d{1,3}(?:[.\s]\d{3})*(?:,\d{2})?|\d+(?:,\d{2})?)")
_CITATION_RE = re.compile(r"\[§\s?([\w.-]+)\]")
_PROTOCOL_RE = re.compile(r"\b[0-9A-F]{8}\b")


def _luhn(number: str) -> bool:
    total, parity = 0, len(number) % 2
    for i, ch in enumerate(number):
        d = int(ch)
        if i % 2 == parity:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


def _cpf_valid(cpf: str) -> bool:
    nums = [int(c) for c in cpf]
    if len(set(nums)) == 1:
        return False
    for size in (9, 10):
        check = sum(n * w for n, w in zip(nums[:size], range(size + 1, 1, -1))) * 10 % 11 % 10
        if check != nums[size]:
            return False
    return True


@dataclass
class InputCheck:
    text: str  # mensagem já sem dados sensíveis
    flags: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"sinais": self.flags, "texto_alterado": bool({"cartao_removido", "cpf_removido"} & set(self.flags))}


def screen_input(text: str) -> InputCheck:
    flags = []
    plain = normalize(text)
    if any(p.search(plain) for p in _INJECTION_RE):
        flags.append("tentativa_de_mudar_regras")

    def redact_card(match: re.Match) -> str:
        number = re.sub(r"\D", "", match.group(0))
        if 13 <= len(number) <= 19 and _luhn(number):
            flags.append("cartao_removido")
            return "[número de cartão removido]"
        return match.group(0)

    def redact_cpf(match: re.Match) -> str:
        if _cpf_valid(re.sub(r"\D", "", match.group(0))):
            flags.append("cpf_removido")
            return "[CPF removido]"
        return match.group(0)

    clean = _CPF_RE.sub(redact_cpf, _CARD_RE.sub(redact_card, text))
    return InputCheck(clean, sorted(set(flags)))


def mask_contact(text: str) -> str:
    """'leticia.rocha@jmail.com' -> 'l***@jmail.com'; '(67) 98432-1098' -> '(67) ****-1098'."""
    text = _EMAIL_RE.sub(lambda m: f"{m.group(1)}***@{m.group(2)}", text)
    return _PHONE_RE.sub(lambda m: f"{m.group(1)}****-{m.group(2)}", text)


def mask_value(value):
    """Mascara contatos dentro de qualquer estrutura JSON (dict, lista, texto)."""
    if isinstance(value, str):
        return mask_contact(value)
    if isinstance(value, dict):
        return {k: mask_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [mask_value(v) for v in value]
    return value


def _cents(raw: str) -> int:
    raw = raw.replace(" ", "").replace(".", "")
    reais, _, cents = raw.partition(",")
    return int(reais) * 100 + int((cents or "0").ljust(2, "0")[:2])


def _json_numbers(value) -> set[int]:
    if isinstance(value, bool):
        return set()
    if isinstance(value, (int, float)):
        return {round(value * 100)}
    if isinstance(value, str):
        return {_cents(m) for m in _MONEY_RE.findall(value)}
    if isinstance(value, dict):
        value = list(value.values())
    if isinstance(value, list):
        return set().union(*map(_json_numbers, value)) if value else set()
    return set()


def money_values(source: str) -> set[int]:
    """Valores em reais de uma fonte, em centavos.

    Resultado de ferramenta (JSON): todo número conta, porque preço vem cru
    (599.9). Texto livre (manual, conversa): só o que vem escrito como "R$ ...",
    para um horário como 13:00 não virar R$ 13,00.
    """
    try:
        return _json_numbers(json.loads(source))
    except (json.JSONDecodeError, TypeError):
        return {_cents(m) for m in _MONEY_RE.findall(source)}


def citations(text: str) -> list[str]:
    return list(dict.fromkeys(_CITATION_RE.findall(text)))


@dataclass
class ReplyCheck:
    issues: list[str]

    @property
    def ok(self) -> bool:
        return not self.issues


def check_reply(reply: str, sources: list[str], retrieved_ids: set[str], valid_ids: set[str]) -> ReplyCheck:
    """Confere a resposta contra as fontes do turno (resultados de ferramentas, trechos e prompt)."""
    issues = []
    known = set().union(*map(money_values, sources)) if sources else set()
    for match in _MONEY_RE.findall(reply):
        if _cents(match) not in known:
            issues.append(f"O valor R$ {match} não aparece em nenhuma consulta deste turno.")
    for cited in citations(reply):
        if cited not in valid_ids:
            issues.append(f"A seção [§{cited}] não existe no manual.")
        elif cited not in retrieved_ids:
            issues.append(f"A seção [§{cited}] foi citada sem ter sido consultada neste turno.")
    if re.search(r"protocolo", reply, re.I):
        for code in _PROTOCOL_RE.findall(reply):
            if not any(code in source for source in sources):
                issues.append(f"O protocolo {code} não foi gerado por nenhum chamado.")
    return ReplyCheck(issues)


def strip_citations(text: str) -> str:
    """Versão para canais sem interface rica (ex.: WhatsApp): sem as marcas [§x]."""
    return re.sub(r"\s?\[§\s?[\w.-]+\]", "", text)
