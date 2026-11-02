# 01 · Busca por faixa de preço e condições de pagamento

**O que mostra:** Busca com filtro de categoria e preço, poucas opções com opções de resposta rápida e PIX/parcelamento calculados em código.

Modelo `openai/gpt-oss-120b` · busca no manual: hibrido · data simulada quarta-feira, 25/03/2026 10:00 · gerado por `scripts/gerar_conversas.py` · na interface: `/?sessao=exemplo-01`

Linhas com `>` mostram o que aconteceu no grafo entre a mensagem e a resposta.

---

**Cliente:** Oi! Tô procurando um violão pra começar a tocar, até uns mil reais

> intenção: produto
> `buscar_produtos(categoria="Violões", preco_max=1000)` → ok

**Lia:** Oi! Tenho boas opções de violões até R$ 1 000 pra quem está começando. No catálogo encontrei **12** modelos. Aqui vão quatro que se encaixam no seu orçamento:

- **Tagima Memphis AC‑39 Nylon** – R$ 429,90 (PIX R$ 408,40) – até 5x de R$ 85,98  
- **Rozini RC‑104 Nylon** – R$ 459,90 (PIX R$ 436,90) – até 5x de R$ 91,98  
- **Shelby SN‑100 Folk Aço** – R$ 549,00 (PIX R$ 521,55) – até 6x de R$ 91,50  
- **Giannini GF‑1R Aço Folk** – R$ 579,90 (PIX R$ 550,90) – até 6x de R$ 96,65  

Qual tipo de corda você prefere? Nylon costuma ser mais suave, ideal pra iniciantes; aço tem som mais brilhante.

*Opções de resposta rápida:* Nylon · Aço

**Cliente:** Prefiro nylon. Quanto fica o Yamaha C40 no PIX? E dá pra parcelar em quantas vezes?

> intenção: produto, politica
> manual (roteador): §3.1, §3, §4.1
> `buscar_produtos(categoria="Violões", termo="Yamaha C40")` → ok

**Lia:** O Yamaha C40 Nylon Natural sai **R$ 569,90 no PIX** (preço à vista com 5 % de desconto) e pode ser parcelado em **até 6x sem juros**, com parcelas de R$ 99,98 cada. 😊

Quer seguir com a compra ou precisa de mais alguma sugestão?

---

**Resultado esperado:**

- [x] chamou buscar_produtos
