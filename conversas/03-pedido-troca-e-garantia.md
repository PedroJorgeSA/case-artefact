# 03 · Pedido, troca fora do prazo e garantia (conversa longa)

**O que mostra:** Verificação de identidade antes de abrir o pedido, prazos de troca calculados a partir das datas do pedido, aplicação do manual (arrependimento x defeito x garantia) e encaminhamento para a equipe com protocolo.

Modelo `openai/gpt-oss-120b` · data simulada quarta-feira, 25/03/2026 10:00 · gerado por `scripts/gerar_conversas.py` · na interface: `/?sessao=exemplo-03`

---

**Cliente:** Oi, sou a Letícia. Comprei um violão com vocês e queria trocar, não gostei muito do som

**Lia:** Oi Letícia! 😊 Entendo, vamos ver a troca do violão. Você pode me passar o número do pedido e o e‑mail ou telefone cadastrado? Assim confirmo os prazos e a disponibilidade do modelo que você quer.

**Cliente:** É o pedido 7, meu email é leticia.rocha@jmail.com

> `consultar_pedido(email_ou_telefone="leticia.rocha@jmail.com", numero_pedido=7)` → ok

**Lia:** Letícia, o prazo para troca por preferência (quando o cliente não gostou do som) é de **7 dias corridos após o recebimento**. Como o violão foi entregue em 17/02/2026, já se passaram 36 dias, então não conseguimos fazer a troca por esse motivo.

Se houver algum **defeito de fabricação**, ainda temos até 30 dias para a troca, mas pelo que você descreveu parece ser apenas a questão do timbre. Caso queira avaliar se há algum problema técnico, podemos abrir um chamado para a equipe analisar e, se for o caso, acionar a garantia legal (90 dias).

Posso registrar o seu pedido para que a gente verifique a situação e encontre a melhor solução? Me avise se deseja seguir assim ou se prefere conversar sobre outro modelo que possa atender melhor ao seu som.

**Cliente:** Entendi... mas na verdade o braço também está empenando, acho que é defeito

**Lia:** Entendo, Letícia. O braço empenado caracteriza um possível defeito de fabricação. O prazo de troca por defeito é de **30 dias corridos após a compra**, e já ultrapassamos esse período. Porém você ainda está dentro da **garantia legal de 90 dias** contra defeitos.

Vou abrir um chamado para a nossa equipe de garantia analisar o caso e providenciar a solução (reparo ou substituição, conforme o diagnóstico). Você receberá o número do protocolo e a previsão de retorno em até **24 horas úteis**.

Posso registrar o chamado agora? Se precisar, inclua algum detalhe extra ou foto do braço para agilizar a avaliação.

**Cliente:** Pode abrir sim, por favor

> `encaminhar_para_atendente(contato_cliente="leticia.rocha@jmail.com", motivo="garantia", numero_pedido=7, resumo="Violão Shelby SU-21S Soprano Sunburst com braço empenado, possivelmente defeito de fabricação. Cliente solicita garantia.")` → ok

**Lia:** ✅ Chamado aberto! Protocolo **B628E673**. Nossa equipe de garantia vai analisar o braço empenado do seu violão e entra em contato dentro de 24 horas úteis, pelo e‑mail ou telefone que você informou.

Enquanto isso, se precisar de mais alguma coisa – outro modelo, informações de entrega ou qualquer dúvida – é só falar. Estou aqui!
