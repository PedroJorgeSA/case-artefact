# 03 · Pedido, troca fora do prazo e garantia (conversa longa)

**O que mostra:** Verificação de identidade antes de abrir o pedido, prazos calculados em código, regras do manual recuperadas e citadas (arrependimento x defeito x garantia) e chamado para a equipe com protocolo.

Modelo `openai/gpt-oss-120b` · busca no manual: hibrido · data simulada quarta-feira, 25/03/2026 10:00 · gerado por `scripts/gerar_conversas.py` · na interface: `/?sessao=exemplo-03`

Linhas com `>` mostram o que aconteceu no grafo entre a mensagem e a resposta.

---

**Cliente:** Oi, sou a Letícia. Comprei um violão com vocês e queria trocar, não gostei muito do som

> intenção: pedido, politica
> manual (roteador): §4.4, §4.3, §4.2

**Lia:** Oi Letícia! Podemos trocar o violão, desde que esteja dentro do prazo de até **7 dias** e o produto esteja em perfeito estado, na embalagem original, e haja estoque do modelo que você deseja [§4.3]. 

Para avançarmos, preciso do número do seu pedido e do e‑mail ou telefone cadastrado. Você pode me passar esses dados?

<sub>[§4.3] 4. Trocas e devoluções › 4.3 Troca por preferência</sub><br>

**Cliente:** É o pedido 7, meu email é leticia.rocha@jmail.com

> intenção: pedido
> `consultar_pedido(email_ou_telefone="l***@jmail.com", numero_pedido=7)` → ok
> verificação: refazer (A seção [§4.3] foi citada sem ter sido consultada neste turno.)
> verificação: corrigida

**Lia:** Oi Letícia, tudo bem? 😊

Verifiquei seu pedido e, como já se passaram **36 dias** desde a entrega, o prazo de troca por preferência (até 7 dias) já expirou. Por isso, não conseguimos fazer a troca do violão nesse caso.

Se quiser, posso abrir um chamado para a nossa equipe analisar uma solução alternativa (por exemplo, um possível reembolso ou outra forma de atender sua necessidade). Você gostaria que eu registrasse o chamado?

**Cliente:** Entendi... mas na verdade o braço também está empenando, acho que é defeito

> intenção: politica, reclamacao
> manual (roteador): §4.2, §8

**Lia:** Entendo, Letícia. O braço empenado caracteriza um possível defeito de fabricação. A troca por defeito é aceita em até **30 dias corridos após a compra** [§4.2]. Como já se passaram mais de 30 dias, a garantia legal de **90 dias** ainda cobre o problema [§8], e podemos acionar o fabricante para a reparação ou substituição.

Posso abrir um chamado para a nossa equipe entrar em contato com a fabricante e orientar o procedimento de garantia. Você gostaria que eu registrasse o chamado?

<sub>[§4.2] 4. Trocas e devoluções › 4.2 Troca por defeito</sub><br>
<sub>[§8] 8. Garantia</sub><br>

**Cliente:** Pode abrir sim, por favor

> intenção: reclamacao
> manual (roteador): §4.2, §8
> `encaminhar_para_atendente(contato_cliente="l***@jmail.com", motivo="garantia", numero_pedido=7, resumo="Violão Shelby SU-21S Soprano Sunburst com braço empenado, possivelmente defeito de fabricação. Cliente solicita acionamento de garantia para reparo/substituição.")` → ok

**Lia:** ✅ Chamado aberto! Seu protocolo é **C47F1869** e a equipe retornará em até **24 horas úteis**. Qualquer coisa, é só chamar aqui. Boa música!

---

**Resultado esperado:**

- [x] chamou consultar_pedido
- [x] chamou encaminhar_para_atendente
- [x] citou §4.2
