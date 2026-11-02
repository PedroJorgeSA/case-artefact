// Empório da Música · Atendimento
// Chat com a Lia + painel com cada consulta que ela fez. A resposta do servidor
// chega em eventos (SSE): sessão, espera pelo limite do Groq, ferramenta, resposta.

const $ = (selector) => document.querySelector(selector);

const thread = $("#thread");
const intro = $("#intro");
const input = $("#input");
const sendButton = $("#send");
const traceBody = $("#trace-body");
const traceEmpty = $("#trace-empty");

const STORAGE_KEY = "emporio.session";

const state = {
  sessionId: null,
  busy: false,
  steps: 0,
  turns: 0,
  turnText: null, // rótulo da mensagem, desenhado só quando ela gera a primeira consulta
};

const TOOLS = {
  buscar_produtos: { title: "Busca no catálogo", icon: '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>' },
  detalhes_produto: { title: "Detalhes do produto", icon: '<path d="M12.586 2.586A2 2 0 0 0 11.172 2H4a2 2 0 0 0-2 2v7.172a2 2 0 0 0 .586 1.414l8.704 8.704a2.426 2.426 0 0 0 3.42 0l6.58-6.58a2.426 2.426 0 0 0 0-3.42z"/><circle cx="7.5" cy="7.5" r=".5"/>' },
  listar_categorias: { title: "Categorias da loja", icon: '<path d="M3 6h.01"/><path d="M3 12h.01"/><path d="M3 18h.01"/><path d="M8 6h13"/><path d="M8 12h13"/><path d="M8 18h13"/>' },
  consultar_pedido: { title: "Consulta de pedido", icon: '<path d="M11 21.73a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73z"/><path d="M12 22V12"/><path d="m3.3 7 8.7 5 8.7-5"/>' },
  encaminhar_para_atendente: { title: "Chamado para a equipe", icon: '<path d="M3 11h3a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-5Zm0 0a9 9 0 1 1 18 0m0 0v5a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3Z"/><path d="M21 16v2a4 4 0 0 1-4 4h-5"/>' },
};

const PENDING_TEXT = {
  buscar_produtos: "Procurando no catálogo",
  detalhes_produto: "Abrindo os detalhes do produto",
  listar_categorias: "Conferindo as categorias",
  consultar_pedido: "Consultando o pedido",
  encaminhar_para_atendente: "Abrindo um chamado para a equipe",
};

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

function icon(paths) {
  return `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">${paths}</svg>`;
}

function pad(n) {
  return String(n).padStart(2, "0");
}

function storage(action, value) {
  try {
    if (action === "get") return localStorage.getItem(STORAGE_KEY);
    if (action === "set") localStorage.setItem(STORAGE_KEY, value);
    if (action === "clear") localStorage.removeItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

// Markdown mínimo: negrito e listas com hífen. Tudo é escapado antes.
function renderText(text) {
  const blocks = [];
  let list = null;
  for (const raw of String(text).split("\n")) {
    const line = raw.trim();
    const item = line.match(/^(?:[-•*]|\d+[.)])\s+(.*)$/);
    if (item) {
      list ??= [];
      list.push(item[1]);
      continue;
    }
    if (list) {
      blocks.push(`<ul>${list.map((li) => `<li>${inline(li)}</li>`).join("")}</ul>`);
      list = null;
    }
    if (line) blocks.push(`<p>${inline(line)}</p>`);
  }
  if (list) blocks.push(`<ul>${list.map((li) => `<li>${inline(li)}</li>`).join("")}</ul>`);
  return blocks.join("");
}

function inline(text) {
  return esc(text).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
}

function scrollThread() {
  thread.scrollTop = thread.scrollHeight;
}

// ---------- conversa ----------

function addUser(text) {
  intro.hidden = true;
  const msg = el("div", "msg msg-user enter");
  msg.append(el("div", "bubble", esc(text)));
  thread.append(msg);
  state.turns += 1;
  state.turnText = text;
  scrollThread();
}

function addReply(text, stepNumbers) {
  const msg = el("div", "msg msg-lia enter");
  msg.append(el("span", "who", "Lia"));
  msg.append(el("div", "bubble", renderText(text)));
  if (stepNumbers.length) {
    const note = el("div", "footnote");
    note.append(document.createTextNode("consultou"));
    for (const n of stepNumbers) {
      const link = el("button", "", pad(n));
      link.type = "button";
      link.title = "Ver no painel";
      link.addEventListener("click", () => focusStep(n));
      note.append(link);
    }
    msg.append(note);
  }
  thread.append(msg);
  scrollThread();
}

function addPending() {
  const msg = el("div", "msg msg-lia pending enter");
  msg.append(el("span", "who", "Lia"));
  const bubble = el("div", "bubble");
  const text = el("span", "", "Lendo a mensagem");
  const elapsed = el("span", "elapsed", "0s");
  bubble.append(text, elapsed);
  msg.append(bubble);
  thread.append(msg);
  scrollThread();

  const started = Date.now();
  let waitUntil = 0;
  const timer = setInterval(() => {
    const seconds = Math.round((Date.now() - started) / 1000);
    const left = Math.ceil((waitUntil - Date.now()) / 1000);
    elapsed.textContent = left > 0 ? `${seconds}s · libera em ${left}s` : `${seconds}s`;
  }, 500);

  return {
    say(value) { text.textContent = value; },
    wait(seconds) {
      waitUntil = Date.now() + seconds * 1000;
      text.textContent = "Aguardando o limite gratuito do Groq";
    },
    remove() { clearInterval(timer); msg.remove(); },
  };
}

function addError(message, retryText) {
  const msg = el("div", "msg msg-lia msg-error enter");
  msg.append(el("span", "who", "Erro"));
  const bubble = el("div", "bubble", `<p>${esc(message)}</p>`);
  if (retryText) {
    const retry = el("button", "btn btn-secondary retry", "Tentar de novo");
    retry.type = "button";
    retry.addEventListener("click", () => {
      msg.remove();
      send(retryText, { echo: false });
    });
    bubble.append(retry);
  }
  msg.append(bubble);
  thread.append(msg);
  scrollThread();
}

// ---------- painel de consultas ----------

function addTurnLabel() {
  if (state.turnText === null) return;
  const text = state.turnText;
  const short = text.length > 48 ? `${text.slice(0, 48).trim()}…` : text;
  traceBody.append(el("p", "turn-label", `Mensagem ${state.turns} · “${esc(short)}”`));
  state.turnText = null;
}

function updateCounts() {
  traceEmpty.hidden = state.steps > 0;
  $("#trace-count").textContent = state.steps === 1 ? "1 consulta" : `${state.steps} consultas`;
  $("#trace-toggle-count").textContent = state.steps;
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
    };
    parts.push(labels[key] ?? `${key.replaceAll("_", " ")}: ${value}`);
  }
  return parts.join(" · ") || "sem parâmetros";
}

function productTag(p) {
  const terms = p.condicoes_pagamento;
  const termsText = terms
    ? `PIX ${terms.preco_pix} · até ${terms.parcelamento_max_sem_juros}x de ${terms.valor_parcela}`
    : "";
  const flag = p.disponibilidade && p.disponibilidade !== "disponível"
    ? ` · <span class="tag-flag">${esc(p.disponibilidade)}</span>` : "";
  const price = p.promocao ? p.promocao.preco_promocional : p.preco;
  const was = p.promocao
    ? `<span class="tag-was"><s>${esc(p.promocao.preco_original)}</s><span class="off">−${esc(p.promocao.desconto_percentual)}%</span></span>`
    : "<span></span>";
  return `<div class="tag">
    <span class="tag-name">${esc(p.nome)}</span><span class="tag-price">${esc(price)}</span>
    <span class="tag-terms">${esc(termsText)}${flag}</span>${was}
  </div>`;
}

const ORDER_STEPS = ["Pago", "Enviado", "Entregue"];

function orderProgress(status) {
  if (status.startsWith("entregue")) return 3;
  if (status.startsWith("enviado")) return 2;
  if (status.startsWith("pagamento confirmado")) return 1;
  return 0;
}

function renderResult(name, result, isError) {
  if (isError) {
    if (name === "consultar_pedido") {
      return '<p class="denied">Número ou contato não conferem. Nenhum dado do pedido foi liberado.</p>';
    }
    return `<p class="denied">${esc(result.erro)}</p>`;
  }

  if (name === "buscar_produtos") {
    const items = result.produtos || [];
    if (!items.length) return '<p class="denied">Nenhum produto encontrado com esses filtros.</p>';
    const shown = items.slice(0, 6);
    const total = result.total_encontrado ?? items.length;
    const more = total > shown.length ? `<div class="tags-more">e mais ${total - shown.length}</div>` : "";
    return `<div class="tags">${shown.map(productTag).join("")}${more}</div>`;
  }

  if (name === "detalhes_produto") {
    const spec = result.especificacoes ? `<p class="spec">${esc(result.especificacoes)}</p>` : "";
    return `<div class="tags">${productTag(result)}</div>${spec}`;
  }

  if (name === "listar_categorias") {
    return `<div class="cats">${(result.categorias || []).map((c) => {
      const empty = !c.produtos_no_catalogo;
      return `<div class="${empty ? "is-empty" : ""}"><span>${esc(c.categoria)}</span><span class="count">${empty ? "sem produtos" : c.produtos_no_catalogo}</span></div>`;
    }).join("")}</div>`;
  }

  if (name === "consultar_pedido") {
    const done = orderProgress(result.status || "");
    const cancelled = (result.status || "").startsWith("cancelado");
    const track = cancelled ? "" : `
      <div class="track">${ORDER_STEPS.map((_, i) => `<span class="${i < done ? "is-done" : ""}"></span>`).join("")}</div>
      <div class="track-labels">${ORDER_STEPS.map((s, i) => `<span class="${i === done - 1 ? "is-current" : ""}">${s}</span>`).join("")}</div>`;
    const facts = [
      ["Status", esc(result.status)],
      ["Rastreio", result.codigo_rastreio ? `<span class="mono">${esc(result.codigo_rastreio)}</span>` : null],
      ["Previsão", esc(result.previsao_entrega)],
      ["Valor", `<span class="mono">${esc(result.valor_total)}</span>`],
      ["Pagamento", esc(result.forma_pagamento)],
      ["Itens", (result.itens || []).map((i) => `${i.quantidade}× ${esc(i.produto)}`).join("<br>")],
    ].filter(([, v]) => v);
    return `<div class="order">
      <div class="order-head"><span>Pedido ${esc(result.pedido)} · ${esc(result.cliente_primeiro_nome)}</span><span>${esc(result.data_compra)}</span></div>
      ${track}
      <dl class="facts">${facts.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("")}</dl>
    </div>`;
  }

  if (name === "encaminhar_para_atendente") {
    return `<div class="ticket">
      <span class="label">Protocolo</span>
      <div class="ticket-code">${esc(result.protocolo)}</div>
      <p>Retorno ${esc(result.prazo_retorno)}</p>
    </div>`;
  }

  return "";
}

function addStep(event) {
  addTurnLabel();
  state.steps += 1;
  const n = state.steps;
  const meta = TOOLS[event.name] || { title: event.name, icon: "" };
  const step = el("article", "step enter");
  step.id = `step-${n}`;
  const status = event.is_error
    ? `<span class="step-status is-error">${event.name === "consultar_pedido" ? "negado" : "erro"}</span>`
    : '<span class="step-status">ok</span>';
  const resumo = event.name === "encaminhar_para_atendente" && event.args?.resumo
    ? `<p class="spec">${esc(event.args.resumo)}</p>` : "";
  step.innerHTML = `
    <div class="step-head"><span class="step-index">${pad(n)}</span>${icon(meta.icon)}<span class="step-title">${esc(meta.title)}</span>${status}</div>
    <p class="step-args">${esc(event.name)} · ${esc(formatArgs(event.name, event.args))}</p>
    <div class="step-result">${renderResult(event.name, event.result || {}, event.is_error)}${resumo}</div>
    <details><summary>Resposta completa</summary><pre>${esc(JSON.stringify(event.result, null, 2))}</pre></details>`;
  traceBody.append(step);
  if (n === 1) $("#testdata").open = false;
  updateCounts();
  traceBody.scrollTop = traceBody.scrollHeight;
  return n;
}

function focusStep(n) {
  const step = document.getElementById(`step-${n}`);
  if (!step) return;
  if (window.matchMedia("(max-width: 960px)").matches) document.body.classList.add("show-trace");
  step.scrollIntoView({ behavior: "smooth", block: "center" });
  step.classList.add("is-focused");
  setTimeout(() => step.classList.remove("is-focused"), 1600);
}

// ---------- envio ----------

function setBusy(busy) {
  state.busy = busy;
  sendButton.disabled = busy || !input.value.trim();
  input.placeholder = busy ? "A Lia está respondendo" : "Escreva como um cliente";
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
  if (state.busy || !text.trim()) return;
  if (echo) addUser(text);
  setBusy(true);
  const pending = addPending();
  const stepNumbers = [];
  let finished = false;

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
      } else if (event.type === "waiting") {
        pending.wait(event.seconds);
      } else if (event.type === "tool") {
        stepNumbers.push(addStep(event));
        pending.say(PENDING_TEXT[event.name] || "Consultando");
      } else if (event.type === "reply") {
        pending.remove();
        addReply(event.text, stepNumbers);
        finished = true;
      } else if (event.type === "error") {
        throw new Error(event.message);
      }
    }
    if (!finished) throw new Error("A conexão terminou antes da resposta.");
  } catch (error) {
    pending.remove();
    addError(error.message || "Falha de conexão com o servidor.", text);
  } finally {
    setBusy(false);
    input.focus();
  }
}

// ---------- sessão ----------

function resetUI() {
  thread.querySelectorAll(".msg").forEach((n) => n.remove());
  traceBody.querySelectorAll(".step, .turn-label").forEach((n) => n.remove());
  intro.hidden = false;
  $("#testdata").open = true;
  state.steps = 0;
  state.turns = 0;
  state.turnText = null;
  updateCounts();
}

async function restore(sessionId) {
  const response = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}`);
  if (!response.ok) return false;
  const { items } = await response.json();
  state.sessionId = sessionId;
  let stepNumbers = [];
  for (const item of items) {
    if (item.type === "user") {
      addUser(item.text);
      stepNumbers = [];
    } else if (item.type === "tool") {
      stepNumbers.push(addStep(item));
    } else if (item.type === "reply") {
      addReply(item.text, stepNumbers);
      stepNumbers = [];
    }
  }
  document.querySelectorAll(".enter").forEach((n) => n.classList.remove("enter"));
  return true;
}

async function loadInfo() {
  try {
    const info = await (await fetch("/api/info")).json();
    $("#clock-text").textContent = `${info.agora} · ${info.status}`;
    $("#clock-dot").classList.toggle("is-open", info.loja_aberta);
    $("#model").textContent = info.modelo;
  } catch {
    $("#clock-text").textContent = "Servidor indisponível";
  }
}

// ---------- eventos ----------

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
  button.addEventListener("click", () => send(button.textContent.trim()));
});

$("#new-chat").addEventListener("click", () => {
  if (state.busy) return;
  state.sessionId = null;
  storage("clear");
  history.replaceState(null, "", location.pathname);
  resetUI();
  input.focus();
});

$("#trace-toggle").addEventListener("click", () => document.body.classList.toggle("show-trace"));

(async function init() {
  setBusy(false);
  loadInfo();
  // ?sessao=<id> abre uma conversa salva (útil para demonstrar exemplos).
  const fromUrl = new URLSearchParams(location.search).get("sessao");
  const saved = fromUrl || storage("get");
  if (saved && !(await restore(saved))) storage("clear");
  input.focus();
})();
