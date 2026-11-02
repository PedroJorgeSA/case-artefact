// Empório da Música · Atendimento
// À esquerda, a conversa como o cliente vê. À direita, o percurso de cada
// mensagem no grafo: nós, intenção, trechos do manual, consultas, tokens e a
// checagem da resposta. Tudo chega pelos mesmos eventos (SSE) que o servidor
// também guarda, então recarregar a página redesenha as duas colunas.

const $ = (selector, root = document) => root.querySelector(selector);

const thread = $("#thread");
const intro = $("#intro");
const input = $("#input");
const sendButton = $("#send");
const trace = $("#trace");
const announcer = $("#announcer");

const STORAGE_KEY = "emporio.session";
const TOKEN_LIMIT = 8000; // por chamada, no plano gratuito do Groq
const INTENT_THRESHOLD = 0.4;

const state = {
  sessionId: null,
  busy: false,
  turns: [],
  manual: null,
  scenarioRunning: false,
};

const NODES = {
  guard: { x: 32, y: 36, label: "guard", stage: "Conferindo a mensagem" },
  route: { x: 98, y: 36, label: "route", stage: "Entendendo o pedido" },
  retrieve: { x: 164, y: 88, label: "retrieve", stage: "Buscando no manual" },
  agent: { x: 232, y: 36, label: "agent", stage: "Pensando na resposta" },
  tools: { x: 232, y: 88, label: "tools", stage: "Consultando os dados" },
  verify: { x: 300, y: 36, label: "verify", stage: "Conferindo valores e fontes" },
  finalize: { x: 366, y: 36, label: "finalize", stage: "Finalizando" },
};
const HALF = 29; // meia largura de um nó no desenho do grafo
const EDGES = [
  ["guard", "route"], ["route", "agent"], ["route", "retrieve"], ["retrieve", "agent"],
  ["agent", "tools"], ["tools", "agent"], ["agent", "verify"], ["verify", "agent"], ["verify", "finalize"],
];

const TOOL_TITLES = {
  buscar_produtos: "Busca no catálogo",
  detalhes_produto: "Detalhes do produto",
  listar_categorias: "Categorias da loja",
  consultar_pedido: "Consulta de pedido",
  encaminhar_para_atendente: "Chamado para a equipe",
};
const TOOL_STAGES = {
  buscar_produtos: "Procurando no catálogo",
  detalhes_produto: "Abrindo os detalhes do produto",
  listar_categorias: "Conferindo as categorias",
  consultar_pedido: "Consultando o pedido",
  encaminhar_para_atendente: "Abrindo um chamado para a equipe",
};
const INTENT_LABELS = {
  produto: "produto", pedido: "pedido", politica: "política",
  reclamacao: "reclamação", saudacao: "saudação", fora_escopo: "fora do escopo",
};
const CHECK_BADGES = {
  aprovada: ["ok", "valores e fontes conferidos"],
  corrigida: ["fixed", "corrigida pela checagem"],
  alerta: ["warn", "checagem com alerta"],
};
const FEEDBACK_REASONS = ["Valor errado", "Regra errada", "Não entendeu", "Queria uma pessoa"];

// ---------- utilidades ----------

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function el(tag, className, html) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (html !== undefined) node.innerHTML = html;
  return node;
}

const pad = (n) => String(n).padStart(2, "0");
const seconds = (ms) => (ms >= 1000 ? `${(ms / 1000).toFixed(1).replace(".", ",")} s` : `${ms} ms`);
const thousands = (n) => Number(n || 0).toLocaleString("pt-BR");
const plain = (text) => String(text).normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();

function storage(action, value) {
  try {
    if (action === "get") return localStorage.getItem(STORAGE_KEY);
    if (action === "set") localStorage.setItem(STORAGE_KEY, value);
    if (action === "clear") localStorage.removeItem(STORAGE_KEY);
  } catch {
    return null;
  }
  return null;
}

function announce(text) {
  announcer.textContent = "";
  setTimeout(() => { announcer.textContent = text; }, 100);
}

const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const isNarrow = () => window.matchMedia("(max-width: 960px)").matches;

// Markdown mínimo: negrito, listas, tabelas e citações [§x]. Tudo é escapado antes.
function inline(text, { cites = false } = {}) {
  let html = esc(text).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/`(.+?)`/g, '<span class="mono">$1</span>');
  if (cites) {
    html = html.replace(/\s?\[§\s?([\w.-]+)\]/g, (_, id) =>
      ` <button type="button" class="cite" data-cite="${esc(id)}" aria-label="Fonte: seção ${esc(id)} do manual">§${esc(id)}</button>`);
  }
  return html;
}

function renderMarkdown(text, options = {}) {
  const blocks = [];
  let list = null;
  let table = null;
  const flushList = () => {
    if (list) blocks.push(`<ul>${list.map((li) => `<li>${inline(li, options)}</li>`).join("")}</ul>`);
    list = null;
  };
  const flushTable = () => {
    if (table) blocks.push(`<table>${table.map((row) => `<tr>${row.map((c) => `<td>${inline(c, options)}</td>`).join("")}</tr>`).join("")}</table>`);
    table = null;
  };
  for (const raw of String(text).split("\n")) {
    const line = raw.trim();
    if (line.startsWith("|")) {
      flushList();
      if (/^\|[\s|:-]+\|$/.test(line)) continue;
      table ??= [];
      table.push(line.slice(1, -1).split("|").map((c) => c.trim()));
      continue;
    }
    flushTable();
    const item = line.match(/^(?:[-•*]|\d+[.)])\s+(.*)$/);
    if (item) {
      list ??= [];
      list.push(item[1]);
      continue;
    }
    flushList();
    if (line) blocks.push(`<p>${inline(line, options)}</p>`);
  }
  flushList();
  flushTable();
  return blocks.join("");
}

function scrollThread() {
  thread.scrollTo({ top: thread.scrollHeight, behavior: reducedMotion() ? "auto" : "smooth" });
}

// ---------- grafo ----------

function drawGraph() {
  const svg = $("#graph");
  const edge = ([a, b]) => {
    const from = NODES[a];
    const to = NODES[b];
    let d;
    if (a === "verify" && b === "agent") d = `M${from.x} ${from.y - 11} C ${from.x} 4, ${to.x} 4, ${to.x} ${to.y - 11}`;
    else if (a === "agent" && b === "tools") d = `M${from.x - 8} ${from.y + 11} L ${to.x - 8} ${to.y - 11}`;
    else if (a === "tools" && b === "agent") d = `M${from.x + 8} ${from.y - 11} L ${to.x + 8} ${to.y + 11}`;
    else if (a === "route" && b === "retrieve") d = `M${from.x} ${from.y + 11} C ${from.x} ${to.y}, ${to.x - HALF - 20} ${to.y}, ${to.x - HALF} ${to.y}`;
    else if (a === "retrieve" && b === "agent") d = `M${from.x + HALF} ${from.y} C ${to.x - HALF - 4} ${from.y}, ${to.x - HALF - 4} ${to.y + 11}, ${to.x - HALF + 12} ${to.y + 11}`;
    else d = `M${from.x + HALF} ${from.y} L ${to.x - HALF} ${to.y}`;
    return `<path class="g-edge" data-edge="${a}>${b}" d="${d}"/>`;
  };
  const node = ([key, n]) =>
    `<g class="g-node" data-node="${key}"><rect x="${n.x - HALF}" y="${n.y - 11}" width="${HALF * 2}" height="22" rx="4"/><text x="${n.x}" y="${n.y}">${n.label}</text></g>`;
  svg.innerHTML = `${EDGES.map(edge).join("")}<text class="g-loop" x="244" y="10">corrige 1×</text>${Object.entries(NODES).map(node).join("")}`;
}

function paintGraph(turn) {
  const svg = $("#graph");
  svg.querySelectorAll(".is-on, .is-active").forEach((n) => n.classList.remove("is-on", "is-active"));
  if (!turn) return;
  const path = turn.path;
  path.forEach((name, i) => {
    svg.querySelector(`[data-node="${name}"]`)?.classList.add("is-on");
    if (i > 0) svg.querySelector(`[data-edge="${path[i - 1]}>${name}"]`)?.classList.add("is-on");
  });
  if (turn.active) svg.querySelector(`[data-node="${turn.active}"]`)?.classList.add("is-active");
}

// ---------- turno no painel ----------

function createTurn(text) {
  const index = state.turns.length + 1;
  const root = el("article", "turn");
  root.id = `turn-${index}`;
  const head = el("div", "turn-head");
  head.innerHTML = `<span class="turn-index">${pad(index)}</span><span class="turn-text">${esc(text)}</span><span class="turn-stats"></span>`;
  const timeline = el("div", "section");
  timeline.innerHTML = '<div class="section-label"><b>Linha do tempo</b><span class="total"></span></div><div class="waterfall"></div>';
  const body = el("div");
  root.append(head, timeline, body);
  trace.append(root);
  $("#trace-empty").hidden = true;
  $("#turn-count").textContent = index;

  const turn = {
    index, text, root, body, path: [], active: null, steps: [], waits: [], llm: [],
    tools: [], retrieved: new Set(), cited: new Set(), reply: null, msg: null,
  };
  head.addEventListener("click", () => paintGraph(turn));
  state.turns.push(turn);
  return turn;
}

function section(turn, label, aside = "") {
  const node = el("div", "section");
  node.innerHTML = `<div class="section-label"><b>${label}</b><span>${aside}</span></div>`;
  turn.body.append(node);
  return node;
}

function renderWaterfall(turn) {
  const total = Math.max(1, turn.reply?.t ?? Math.max(0, ...turn.steps.map((s) => s.end ?? s.start)));
  const rows = [];
  for (const step of turn.steps) {
    const end = step.end ?? total;
    const left = (step.start / total) * 100;
    const width = Math.max(0.6, ((end - step.start) / total) * 100);
    const kind = step.node === "agent" ? "is-model" : "";
    rows.push(`<span class="node-name">${step.node}</span><span class="lane"><span class="bar ${kind}" style="left:${left}%;width:${width}%"></span></span><span class="ms">${step.end === undefined ? "…" : seconds(step.end - step.start)}</span>`);
  }
  for (const wait of turn.waits) {
    const left = (wait.t / total) * 100;
    const width = Math.min(100 - left, ((wait.seconds * 1000) / total) * 100);
    rows.push(`<span class="node-name">espera</span><span class="lane"><span class="bar is-wait" style="left:${left}%;width:${width}%"></span></span><span class="ms">${seconds(wait.seconds * 1000)}</span>`);
  }
  $(".waterfall", turn.root).innerHTML = rows.join("");
  $(".total", turn.root).textContent = seconds(total);
  const tokens = turn.llm.reduce((sum, c) => sum + (c.prompt_tokens || 0) + (c.completion_tokens || 0), 0);
  const calls = turn.llm.length;
  $(".turn-stats", turn.root).textContent = `${seconds(total)} · ${calls} chamada${calls === 1 ? "" : "s"} · ${thousands(tokens)} tokens`;
}

function renderRoute(turn, event) {
  const node = section(turn, "Intenção", event.method === "ml" ? "TF-IDF + regressão logística" : "palavras-chave");
  const scores = Object.entries(event.scores || {}).sort((a, b) => b[1] - a[1]);
  const rows = scores.length
    ? scores.map(([label, p]) => {
      const picked = event.intents.includes(label) ? "is-picked" : "";
      return `<span class="${picked}">${INTENT_LABELS[label] || label}</span><span class="lane ${picked}"><span class="bar" style="width:${Math.round(p * 100)}%"></span></span><span class="score">${p.toFixed(2)}</span>`;
    }).join("")
    : event.intents.map((label) => `<span class="is-picked">${INTENT_LABELS[label] || label}</span><span></span><span></span>`).join("");
  node.insertAdjacentHTML("beforeend", `<div class="intents">${rows}</div>`);
  node.insertAdjacentHTML("beforeend", `<p class="query" style="margin-top:6px">${event.retrieval ? `vai ao manual · consulta: “${esc(event.query)}”` : "não precisa do manual: segue direto para o modelo"}</p>`);
}

function renderRetrieval(turn, event) {
  const confidence = { alta: "confiança alta", media: "confiança média", nenhuma: "nada relevante" }[event.confianca] || "";
  const origin = event.source === "ferramenta" ? "Manual, pedido pelo modelo" : "Manual (RAG)";
  const node = section(turn, origin, `${event.modo === "hibrido" ? "BM25 + embeddings · RRF" : "BM25"} · ${confidence}`);
  const extra = event.termos?.length ? ` · sinônimos: ${event.termos.join(", ")}` : "";
  node.insertAdjacentHTML("beforeend", `<p class="query">consulta: “${esc(event.consulta)}”${esc(extra)}</p>`);
  const list = el("div", "chunks");
  if (!event.trechos.length) list.innerHTML = '<p class="denied">Nenhuma seção passou do filtro de relevância. A Lia deve dizer que vai confirmar com a equipe.</p>';
  for (const chunk of event.trechos) {
    turn.retrieved.add(chunk.id);
    const item = el("div", "chunk");
    item.dataset.chunk = chunk.id;
    const sim = chunk.similaridade === null ? "" : ` · cos ${chunk.similaridade.toFixed(2)}`;
    const ranks = `bm25 #${chunk.rank_bm25 ?? "–"}${chunk.rank_denso ? ` · denso #${chunk.rank_denso}` : ""}`;
    item.innerHTML = `<div class="chunk-head"><span class="chunk-id">§${esc(chunk.id)}</span><span class="chunk-title">${esc(chunk.secao.split(" › ").pop())}</span><span class="chunk-tag"></span></div>
      <div class="chunk-scores">rrf ${chunk.score.toFixed(4)} · bm25 ${chunk.bm25.toFixed(1)}${sim} · ${ranks}</div>`;
    list.append(item);
  }
  node.append(list);
}

function formatArgs(name, args) {
  const parts = [];
  for (const [key, value] of Object.entries(args || {})) {
    if (value === null || value === undefined || value === "" || value === false) continue;
    if (name === "encaminhar_para_atendente" && key === "resumo") continue;
    const money = typeof value === "number" ? value.toLocaleString("pt-BR") : value;
    const labels = {
      preco_min: `a partir de R$ ${money}`,
      preco_max: `até R$ ${money}`,
      incluir_indisponiveis: "inclui indisponíveis",
      produto_id: `produto ${value}`,
      numero_pedido: `pedido ${value}`,
      email_ou_telefone: `contato ${value}`,
    };
    parts.push(labels[key] ?? `${key.replaceAll("_", " ")}: ${value}`);
  }
  return parts.join(" · ") || "sem parâmetros";
}

function productRow(p) {
  const terms = p.condicoes_pagamento;
  const price = p.promocao ? p.promocao.preco_promocional : p.preco;
  const termsText = terms ? `PIX ${terms.preco_pix} · até ${terms.parcelamento_max_sem_juros}x de ${terms.valor_parcela} sem juros` : "";
  const flag = p.disponibilidade && p.disponibilidade !== "disponível" ? ` · <span class="flag">${esc(p.disponibilidade)}</span>` : "";
  const was = p.promocao
    ? `<span class="was"><s>${esc(p.promocao.preco_original)}</s><span class="off">−${esc(p.promocao.desconto_percentual)}%</span></span>`
    : "<span></span>";
  return `<div class="product-row"><span class="name">${esc(p.nome)}</span><span class="price num">${esc(price)}</span><span class="terms">${esc(termsText)}${flag}</span>${was}</div>`;
}

const ORDER_STEPS = ["Pago", "Enviado", "Entregue"];

function orderCard(order) {
  const status = order.status || "";
  const done = status.startsWith("entregue") ? 3 : status.startsWith("enviado") ? 2 : status.startsWith("pagamento confirmado") ? 1 : 0;
  const track = status.startsWith("cancelado") ? "" : `
    <div class="track">${ORDER_STEPS.map((_, i) => `<span class="${i < done ? "is-done" : ""}"></span>`).join("")}</div>
    <div class="track-labels">${ORDER_STEPS.map((s, i) => `<span class="${i === done - 1 ? "is-current" : ""}">${s}</span>`).join("")}</div>`;
  const facts = [
    ["Status", esc(status)],
    ["Rastreio", order.codigo_rastreio ? `<span class="mono">${esc(order.codigo_rastreio)}</span>` : null],
    ["Previsão", esc(order.previsao_entrega)],
    ["Valor", `<span class="mono">${esc(order.valor_total)}</span>`],
    ["Itens", (order.itens || []).map((i) => `${i.quantidade}× ${esc(i.produto)}`).join("<br>")],
  ].filter(([, v]) => v);
  return `<div class="card"><div class="card-head"><strong>Pedido ${esc(order.pedido)}</strong><span>${esc(order.data_compra)}</span></div>${track}
    <dl class="facts">${facts.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("")}</dl></div>`;
}

function ticketCard(ticket) {
  return `<div class="card"><div class="card-head"><strong>Chamado aberto para a equipe</strong><span>retorno ${esc(ticket.prazo_retorno)}</span></div>
    <div class="ticket-code">${esc(ticket.protocolo)}</div></div>`;
}

function toolResult(name, result, isError) {
  if (isError) {
    if (name === "consultar_pedido") return '<p class="denied">Número ou contato não conferem. Nenhum dado do pedido foi liberado.</p>';
    return `<p class="denied">${esc(result.erro)}</p>`;
  }
  if (name === "buscar_produtos") {
    const items = result.produtos || [];
    if (!items.length) return '<p class="denied">Nenhum produto com esses filtros.</p>';
    const more = (result.total_encontrado ?? items.length) - items.length;
    return `<div class="attachments">${items.map(productRow).join("")}</div>${more > 0 ? `<p class="query" style="margin:6px 0 0">e mais ${more} no catálogo</p>` : ""}`;
  }
  if (name === "detalhes_produto") return `<div class="attachments">${productRow(result)}</div>`;
  if (name === "listar_categorias") {
    return `<div class="cats">${(result.categorias || []).map((c) => {
      const empty = !c.produtos_no_catalogo;
      return `<div class="${empty ? "is-empty" : ""}"><span>${esc(c.categoria)}</span><span class="mono">${empty ? "0" : c.produtos_no_catalogo}</span></div>`;
    }).join("")}</div>`;
  }
  if (name === "consultar_pedido") return orderCard(result);
  if (name === "encaminhar_para_atendente") return ticketCard(result);
  return "";
}

function renderTool(turn, event) {
  turn.tools.push(event);
  let node = turn.toolSection;
  if (!node) {
    node = section(turn, "Consultas");
    turn.toolSection = node;
  }
  const item = el("div", "tool");
  const status = event.is_error ? (event.name === "consultar_pedido" ? "negado" : "erro") : "ok";
  const summary = event.name === "encaminhar_para_atendente" && event.args?.resumo ? `<p class="query" style="margin-top:6px">resumo para a equipe: ${esc(event.args.resumo)}</p>` : "";
  item.innerHTML = `<div class="tool-head">${esc(TOOL_TITLES[event.name] || event.name)}<span class="tool-status ${event.is_error ? "is-error" : ""}">${status}</span></div>
    <p class="tool-args">${esc(event.name)} · ${esc(formatArgs(event.name, event.args))}</p>
    ${toolResult(event.name, event.result || {}, event.is_error)}${summary}
    <details class="raw"><summary>Resposta completa (contatos mascarados)</summary><pre>${esc(JSON.stringify(event.result, null, 2))}</pre></details>`;
  node.append(item);
}

function renderLlm(turn, event) {
  turn.llm.push(event);
  let node = turn.llmSection;
  if (!node) {
    node = section(turn, "Chamadas ao modelo", `limite de ${thousands(TOKEN_LIMIT)} tokens por chamada`);
    node.append(el("div", "tokens"));
    turn.llmSection = node;
  }
  const prompt = event.prompt_tokens ?? event.estimated_prompt_tokens ?? 0;
  const completion = event.completion_tokens ?? 0;
  const row = el("div");
  row.innerHTML = `<div>${turn.llm.length}ª · ${thousands(prompt)} entrada + ${thousands(completion)} saída · ${seconds(event.ms)}</div>
    <div class="budget" title="Uso do limite por chamada"><span class="prompt" style="left:0;width:${(prompt / TOKEN_LIMIT) * 100}%"></span><span class="completion" style="left:${(prompt / TOKEN_LIMIT) * 100}%;width:${(completion / TOKEN_LIMIT) * 100}%"></span></div>`;
  $(".tokens", node).append(row);
}

function renderCheck(turn, event) {
  const labels = {
    aprovada: "aprovada: todo valor, protocolo e seção citada aparece nas fontes do turno",
    refazer: "reprovada: a resposta voltou ao modelo com o problema",
    corrigida: "aprovada na segunda versão",
    alerta: "enviada com alerta: a segunda versão também não bateu",
  };
  const node = section(turn, "Checagem da resposta", "verify");
  const kind = { aprovada: "ok", corrigida: "fixed", refazer: "warn", alerta: "warn" }[event.status];
  node.insertAdjacentHTML("beforeend", `<div class="check-line"><span class="badge ${kind}">${event.status}</span><span>${labels[event.status] || ""}</span></div>`);
  if (event.issues?.length) node.insertAdjacentHTML("beforeend", `<ul class="issues">${event.issues.map((i) => `<li>${esc(i)}</li>`).join("")}</ul>`);
}

function renderGuard(turn, event) {
  if (!event.sinais?.length) return;
  const labels = {
    tentativa_de_mudar_regras: "tentativa de mudar as regras",
    cartao_removido: "número de cartão removido",
    cpf_removido: "CPF removido",
  };
  const node = section(turn, "Guardrail de entrada", "guard");
  node.insertAdjacentHTML("beforeend", `<div class="flags">${event.sinais.map((s) => `<span class="flag-pill">${labels[s] || s}</span>`).join("")}</div>`);
}

function focusTurn(index) {
  const turn = state.turns[index - 1];
  if (!turn) return;
  selectTab("trace");
  if (isNarrow()) openInspector(true);
  paintGraph(turn);
  turn.root.scrollIntoView({ behavior: reducedMotion() ? "auto" : "smooth", block: "start" });
  turn.root.classList.add("is-focused");
  setTimeout(() => turn.root.classList.remove("is-focused"), 1600);
}

// ---------- conversa ----------

function addUser(text) {
  intro.hidden = true;
  const msg = el("div", "msg msg-user enter");
  const bubble = el("div", "bubble");
  bubble.textContent = text;
  msg.append(bubble);
  thread.append(msg);
  scrollThread();
  return bubble;
}

function addPending() {
  const msg = el("div", "msg msg-lia pending enter");
  msg.innerHTML = '<span class="avatar" aria-hidden="true">L</span><div class="msg-body"><div class="who">Lia</div><div class="text"><span class="stage">Lendo a mensagem</span><span class="elapsed">0 s</span></div></div>';
  thread.append(msg);
  scrollThread();
  const stage = $(".stage", msg);
  const elapsed = $(".elapsed", msg);
  const started = Date.now();
  let waitUntil = 0;
  const timer = setInterval(() => {
    const secs = Math.round((Date.now() - started) / 1000);
    const left = Math.ceil((waitUntil - Date.now()) / 1000);
    elapsed.textContent = left > 0 ? `${secs} s · limite do Groq libera em ${left} s` : `${secs} s`;
  }, 500);
  return {
    say(text) { stage.textContent = text; },
    wait(secs) { waitUntil = Date.now() + secs * 1000; stage.textContent = "Aguardando o limite gratuito do Groq"; },
    remove() { clearInterval(timer); msg.remove(); },
  };
}

// Anexos: o que as consultas devolveram, desenhado para o cliente. Produtos só
// entram se a resposta citar o nome, para o cartão acompanhar o texto.
function attachments(turn, text) {
  const html = [];
  const said = plain(text);
  const products = new Map();
  for (const tool of turn.tools) {
    if (tool.is_error) continue;
    if (tool.name === "buscar_produtos") (tool.result.produtos || []).forEach((p) => products.set(p.produto_id, p));
    if (tool.name === "detalhes_produto") products.set(tool.result.produto_id, tool.result);
  }
  // Com uma ou duas opções a resposta fala de itens específicos e o cartão ajuda;
  // com uma lista maior, o texto já faz esse papel e o cartão só repetiria.
  const mentioned = [...products.values()].filter((p) => said.includes(plain(p.nome).split(" ").slice(0, 3).join(" ")));
  if (mentioned.length && mentioned.length <= 2) html.push(...mentioned.map(productRow));
  const order = turn.tools.find((t) => t.name === "consultar_pedido" && !t.is_error);
  if (order) html.push(orderCard(order.result));
  const ticket = turn.tools.find((t) => t.name === "encaminhar_para_atendente" && !t.is_error);
  if (ticket) html.push(ticketCard(ticket.result));
  return html.length ? `<div class="attachments">${html.join("")}</div>` : "";
}

function addReply(turn, event) {
  const msg = el("div", "msg msg-lia enter");
  const [kind, label] = CHECK_BADGES[event.check?.status] || [];
  msg.innerHTML = `<span class="avatar" aria-hidden="true">L</span><div class="msg-body">
    <div class="who">Lia</div>
    <div class="text">${renderMarkdown(event.text, { cites: true })}</div>
    ${attachments(turn, event.text)}
    ${event.options?.length ? `<div class="options" role="group" aria-label="Respostas rápidas">${event.options.map((o) => `<button type="button" class="option">${esc(o)}</button>`).join("")}</div>` : ""}
    <div class="meta">
      ${kind ? `<span class="badge ${kind}">${label}</span>` : ""}
      <button type="button" class="link-btn turn-link">ver percurso ${pad(turn.index)}</button>
      <span class="feedback" role="group" aria-label="Esta resposta ajudou?">
        <button type="button" data-rating="up" aria-pressed="false">Útil</button>
        <button type="button" data-rating="down" aria-pressed="false">Não ajudou</button>
      </span>
    </div></div>`;
  for (const id of (event.citations || []).map((c) => c.id)) turn.cited.add(id);
  turn.root.querySelectorAll(".chunk").forEach((chunk) => {
    if (turn.cited.has(chunk.dataset.chunk)) {
      chunk.classList.add("is-cited");
      $(".chunk-tag", chunk).textContent = "citada";
    }
  });
  $(".turn-link", msg).addEventListener("click", () => focusTurn(turn.index));
  msg.querySelectorAll(".option").forEach((button) => button.addEventListener("click", () => send(button.textContent)));
  msg.querySelectorAll("[data-rating]").forEach((button) => button.addEventListener("click", () => rate(turn, msg, button)));
  thread.append(msg);
  turn.msg = msg;
  scrollThread();
}

async function rate(turn, msg, button) {
  const rating = button.dataset.rating;
  msg.querySelectorAll("[data-rating]").forEach((b) => b.setAttribute("aria-pressed", String(b === button)));
  $(".reasons", msg)?.remove();
  const post = (reason) => fetch("/api/feedback", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: state.sessionId, turn: turn.index - 1, rating, reason }),
  }).catch(() => {});
  if (rating === "up") {
    post(null);
    announce("Obrigado pela avaliação");
    return;
  }
  const reasons = el("div", "reasons");
  reasons.setAttribute("role", "group");
  reasons.setAttribute("aria-label", "O que deu errado?");
  for (const reason of FEEDBACK_REASONS) {
    const chip = el("button", "", esc(reason));
    chip.type = "button";
    chip.addEventListener("click", () => {
      post(reason);
      reasons.replaceChildren(el("span", "", `Registrado: ${esc(reason.toLowerCase())}.`));
      announce("Avaliação registrada");
    });
    reasons.append(chip);
  }
  $(".meta", msg).append(reasons);
}

function addError(message, retryText) {
  const msg = el("div", "msg msg-lia msg-error enter");
  msg.innerHTML = `<span class="avatar" aria-hidden="true">!</span><div class="msg-body"><div class="who">Erro</div><div class="text"><p>${esc(message)}</p></div></div>`;
  if (retryText) {
    const retry = el("button", "btn btn-secondary", "Tentar de novo");
    retry.type = "button";
    retry.addEventListener("click", () => {
      msg.remove();
      send(retryText, { echo: false });
    });
    $(".msg-body", msg).append(retry);
  }
  thread.append(msg);
  scrollThread();
}

// ---------- eventos do grafo ----------

function handle(turn, event, pending) {
  switch (event.type) {
    case "step":
      if (event.status === "start") {
        turn.steps.push({ node: event.node, start: event.t ?? 0 });
        turn.path.push(event.node);
        turn.active = event.node;
        pending?.say(NODES[event.node]?.stage || "Trabalhando");
      } else {
        const step = [...turn.steps].reverse().find((s) => s.node === event.node && s.end === undefined);
        if (step) step.end = event.t ?? step.start + (event.ms || 0);
        turn.active = null;
      }
      renderWaterfall(turn);
      if (turn === state.turns.at(-1)) paintGraph(turn);
      break;
    case "user":
      if (turn.bubble && turn.bubble.textContent !== event.text) {
        turn.bubble.textContent = event.text; // dado sensível removido antes de chegar ao modelo
        turn.bubble.title = "Dados sensíveis foram removidos antes de enviar ao modelo";
      }
      break;
    case "guard": renderGuard(turn, event); break;
    case "route": renderRoute(turn, event); break;
    case "retrieval": renderRetrieval(turn, event); break;
    case "llm": renderLlm(turn, event); renderWaterfall(turn); break;
    case "waiting":
      turn.waits.push(event);
      pending?.wait(event.seconds);
      renderWaterfall(turn);
      break;
    case "tool":
      renderTool(turn, event);
      pending?.say(TOOL_STAGES[event.name] || "Consultando");
      break;
    case "check": renderCheck(turn, event); break;
    case "reply":
      turn.reply = event;
      pending?.remove();
      addReply(turn, event);
      renderWaterfall(turn);
      paintGraph(turn);
      break;
    default:
      break;
  }
}

// ---------- envio ----------

function setBusy(busy) {
  state.busy = busy;
  sendButton.disabled = busy || !input.value.trim();
  input.placeholder = busy ? "A Lia está respondendo" : "Escreva como um cliente";
  document.querySelectorAll(".option, .suggestion, .run-scenario").forEach((b) => { b.disabled = busy || (b.classList.contains("run-scenario") && state.scenarioRunning); });
}

async function* readEvents(response) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let cut;
    while ((cut = buffer.indexOf("\n\n")) !== -1) {
      const chunk = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      const data = chunk.split("\n").filter((l) => l.startsWith("data: ")).map((l) => l.slice(6)).join("\n");
      if (data) yield JSON.parse(data);
    }
  }
}

async function send(text, { echo = true } = {}) {
  text = String(text).trim();
  if (state.busy || !text) return null;
  const turn = createTurn(text);
  if (echo) turn.bubble = addUser(text);
  setBusy(true);
  announce("A Lia está respondendo");
  const pending = addPending();
  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, session_id: state.sessionId }),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(typeof body.detail === "string" ? body.detail : `Erro ${response.status} no servidor.`);
    }
    for await (const event of readEvents(response)) {
      if (event.type === "session") {
        state.sessionId = event.session_id;
        storage("set", event.session_id);
      } else if (event.type === "error") {
        throw new Error(event.message);
      } else {
        handle(turn, event, pending);
      }
    }
    if (!turn.reply) throw new Error("A conexão terminou antes da resposta.");
    announce("Resposta recebida");
    return turn.reply;
  } catch (error) {
    pending.remove();
    turn.root.remove();
    state.turns.pop();
    $("#turn-count").textContent = state.turns.length;
    addError(error.message || "Falha de conexão com o servidor.", text);
    return null;
  } finally {
    setBusy(false);
    if (!isNarrow()) input.focus();
  }
}

// ---------- sessão ----------

function resetUI() {
  thread.querySelectorAll(".msg").forEach((n) => n.remove());
  trace.querySelectorAll(".turn").forEach((n) => n.remove());
  intro.hidden = false;
  state.turns = [];
  $("#trace-empty").hidden = false;
  $("#turn-count").textContent = "0";
  paintGraph(null);
}

function newChat() {
  state.sessionId = null;
  storage("clear");
  history.replaceState(null, "", location.pathname);
  resetUI();
}

async function restore(sessionId) {
  const response = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}`);
  if (!response.ok) return false;
  const { turns } = await response.json();
  state.sessionId = sessionId;
  for (const events of turns) {
    const user = events.find((e) => e.type === "user");
    const turn = createTurn(user?.text ?? "");
    turn.bubble = addUser(user?.text ?? "");
    for (const event of events) handle(turn, event, null);
  }
  document.querySelectorAll(".enter").forEach((n) => n.classList.remove("enter"));
  return true;
}

// ---------- painel: abas, manual, cenários ----------

function selectTab(name) {
  for (const tab of document.querySelectorAll('[role="tab"]')) {
    const selected = tab.id === `tab-${name}`;
    tab.setAttribute("aria-selected", String(selected));
    tab.tabIndex = selected ? 0 : -1;
    $(`#${tab.getAttribute("aria-controls")}`).hidden = !selected;
  }
}

function openInspector(open) {
  document.body.classList.toggle("show-inspector", open);
  $("#inspector-toggle").setAttribute("aria-expanded", String(open));
}

async function loadManual() {
  if (state.manual) return;
  try {
    state.manual = await (await fetch("/api/manual")).json();
  } catch {
    return;
  }
  $("#manual-note").textContent = `${state.manual.secoes.length} seções indexadas (busca ${state.manual.modo === "hibrido" ? "híbrida: BM25 + embeddings" : "BM25"}). As citações [§] das respostas abrem aqui.`;
  $("#manual").innerHTML = state.manual.secoes.map((s) => `
    <section class="manual-section" id="sec-${esc(s.id)}">
      <h3><span class="chunk-id">§${esc(s.id)}</span><span>${esc(s.secao)}</span></h3>
      <div class="body">${renderMarkdown(s.texto)}</div>
    </section>`).join("");
}

async function openCitation(id, { keep = false } = {}) {
  await loadManual();
  selectTab("manual");
  if (isNarrow()) openInspector(true);
  const target = document.getElementById(`sec-${id}`);
  if (!target) return;
  target.scrollIntoView({ behavior: reducedMotion() || keep ? "auto" : "smooth", block: "start" });
  target.classList.add("is-focused");
  if (!keep) setTimeout(() => target.classList.remove("is-focused"), 2400);
}

async function loadScenarios() {
  let scenarios = [];
  try {
    scenarios = await (await fetch("/api/scenarios")).json();
  } catch {
    return;
  }
  const list = $("#scenarios");
  for (const scenario of scenarios) {
    const item = el("article", "scenario");
    item.innerHTML = `<div class="scenario-head"><span class="chunk-id">${esc(scenario.id)}</span><h3>${esc(scenario.titulo)}</h3>
      <button type="button" class="btn btn-secondary run-scenario">Rodar</button></div>
      <p>${esc(scenario.mostra)}</p><p class="mono">${scenario.mensagens.length} mensagens</p><ul class="checks"></ul>`;
    $(".run-scenario", item).addEventListener("click", () => runScenario(scenario, item));
    list.append(item);
  }
}

async function runScenario(scenario, item) {
  if (state.busy || state.scenarioRunning) return;
  state.scenarioRunning = true;
  const checks = $(".checks", item);
  checks.innerHTML = "<li>rodando…</li>";
  newChat();
  selectTab("trace");
  if (isNarrow()) openInspector(false);
  const replies = [];
  for (const message of scenario.mensagens) {
    const reply = await send(message);
    if (!reply) break;
    replies.push(reply);
  }
  state.scenarioRunning = false;
  setBusy(false);
  try {
    const result = await (await fetch(`/api/scenarios/${scenario.id}/check`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ replies }),
    })).json();
    const done = replies.length === scenario.mensagens.length;
    checks.innerHTML = (done ? "" : '<li class="fail">conversa interrompida</li>')
      + result.checks.map((c) => `<li class="${c.ok ? "pass" : "fail"}">${esc(c.item)}</li>`).join("");
  } catch {
    checks.innerHTML = '<li class="fail">não foi possível conferir</li>';
  }
}

async function loadInfo() {
  try {
    const info = await (await fetch("/api/info")).json();
    $("#clock-text").textContent = `${info.agora} · ${info.status}`;
    $("#clock-dot").classList.toggle("is-open", info.loja_aberta);
    $("#model").textContent = info.modelo.replace(/^openai\//, "");
    $("#search-mode").textContent = info.busca === "hibrido" ? "RAG híbrido" : "RAG BM25";
    $("#router-mode").textContent = info.roteador === "ml" ? "ML (TF-IDF + LR)" : "palavras-chave";
  } catch {
    $("#clock-text").textContent = "servidor indisponível";
  }
}

// ---------- interações ----------

$("#composer").addEventListener("submit", (event) => {
  event.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  autoGrow();
  send(text);
});

function autoGrow() {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 160)}px`;
  sendButton.disabled = state.busy || !input.value.trim();
}

input.addEventListener("input", autoGrow);
input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    $("#composer").requestSubmit();
  }
});

document.querySelectorAll(".suggestion").forEach((button) => {
  button.addEventListener("click", () => send(button.lastChild.textContent));
});

thread.addEventListener("click", (event) => {
  const cite = event.target.closest(".cite");
  if (cite) openCitation(cite.dataset.cite);
});

$("#new-chat").addEventListener("click", () => {
  if (state.busy) return;
  newChat();
  input.focus();
});

document.querySelectorAll('[role="tab"]').forEach((tab) => {
  tab.addEventListener("click", () => {
    const name = tab.id.replace("tab-", "");
    selectTab(name);
    if (name === "manual") loadManual();
  });
  tab.addEventListener("keydown", (event) => {
    if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
    const tabs = [...document.querySelectorAll('[role="tab"]')];
    const next = tabs[(tabs.indexOf(tab) + (event.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
    next.focus();
    next.click();
  });
});

$("#inspector-toggle").addEventListener("click", () => openInspector(!document.body.classList.contains("show-inspector")));
$("#close-inspector").addEventListener("click", () => openInspector(false));

(async function init() {
  drawGraph();
  setBusy(false);
  loadInfo();
  loadScenarios();
  // ?sessao=<id> abre uma conversa salva (útil para demonstrar os exemplos).
  const fromUrl = new URLSearchParams(location.search).get("sessao");
  const saved = fromUrl || storage("get");
  if (saved && !(await restore(saved))) storage("clear");
  // #manual=4.1 abre o manual numa seção; #cenarios abre a aba de cenários.
  const [hashTab, hashValue] = location.hash.slice(1).split("=");
  if (hashTab === "manual") openCitation(hashValue || "1", { keep: true });
  if (hashTab === "cenarios") selectTab("scenarios");
  if (!isNarrow()) input.focus();
})();
