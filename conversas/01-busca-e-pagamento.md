# 01 · Busca por faixa de preço e condições de pagamento

**O que mostra:** Busca com filtro de categoria e preço, resposta curta com poucas opções e PIX/parcelamento calculados em código.

Modelo `openai/gpt-oss-120b` · data simulada quarta-feira, 25/03/2026 10:00 · gerado por `scripts/gerar_conversas.py` · na interface: `/?sessao=exemplo-01`

---

**Cliente:** Oi! Tô procurando um violão pra começar a tocar, até uns mil reais

> `buscar_produtos(categoria="Violões", preco_max=1000)` → ok

**Lia:** Oi! Tudo bem? 😊 Tenho 12 violões na faixa de até R$ 1 000,00. Vou te mostrar os quatro mais baratos e a gente afina a escolha:

- **Tagima Memphis AC‑39 Nylon** – R$ 429,90 (PIX: **R$ 408,40**) – até 5x de R$ 85,98  
- **Rozini RC‑104 Nylon Estudante** – R$ 459,90 (PIX: **R$ 436,90**) – até 5x de R$ 91,98  
- **Shelby SN‑100 Folk Aço** – R$ 549,00 (PIX: **R$ 521,55**) – até 6x de R$ 91,50  
- **Giannini GF‑1R Aço Folk** – R$ 579,90 (PIX: **R$ 550,90**) – até 6x de R$ 96,65  

Você prefere corda de nylon (mais suave, ideal para iniciantes) ou aço (som mais brilhante)? Também me conta se busca algo mais clássico ou moderno, que eu já trago outras opções.

**Cliente:** Prefiro nylon. Quanto fica o Yamaha C40 no PIX? E dá pra parcelar em quantas vezes?

> `detalhes_produto(produto_id=0)` → erro

> `buscar_produtos(categoria="Violões", termo="Yamaha C40")` → ok

**Lia:** O Yamaha C40 Nylon Natural sai por **R$ 599,90** (preço de tabela). No PIX você paga **R$ 569,90** e pode parcelar em até **6x** de R$ 99,98, sem juros.  

Se quiser fechar a compra ou ver mais opções de violões de nylon, é só me avisar! 🎸
