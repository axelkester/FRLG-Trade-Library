/* FRLG Trade Library - vanilla JS frontend. No build step, no framework. */
"use strict";

const TYPE_COLORS = {
  NORMAL: "#9fa19f", FIGHTING: "#ff8000", FLYING: "#81b9ef", POISON: "#a040a0",
  GROUND: "#915121", ROCK: "#afa981", BUG: "#91a119", GHOST: "#704170",
  STEEL: "#60a1b8", MYSTERY: "#68a090", FIRE: "#e62829", WATER: "#2980ef",
  GRASS: "#3fa129", ELECTRIC: "#fac000", PSYCHIC: "#ef4179", ICE: "#3dcef3",
  DRAGON: "#5060e1", DARK: "#624d4e",
};

const state = {
  dex: [],
  types: [],
  filterText: "",
  filterTypes: new Set(),
  availableOnly: false,
  shinyOnly: false,
  sort: "dex",
  trade: null,        // /api/state payload
  currentSpecies: null,
  currentInstance: null,
};

const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
};

function pad3(n) { return String(n).padStart(3, "0"); }

function typeChip(typeName, small) {
  const chip = el("span", "type-chip" + (small ? "" : ""), typeName);
  chip.style.background = TYPE_COLORS[typeName] || "#777";
  chip.dataset.type = typeName;
  return chip;
}

/* ---------- Pokédex grid ---------- */
async function loadDex() {
  const res = await fetch("/api/pokedex");
  const data = await res.json();
  state.dex = data.species;
  const typeSet = new Set();
  for (const s of state.dex) for (const t of s.types) typeSet.add(t);
  state.types = [...typeSet].sort();
  renderTypeFilters();
  renderGrid();
}

function renderTypeFilters() {
  const box = $("#type-filters");
  box.textContent = "";
  for (const type of state.types) {
    const chip = typeChip(type);
    chip.classList.toggle("on", state.filterTypes.has(type));
    chip.addEventListener("click", () => {
      if (state.filterTypes.has(type)) state.filterTypes.delete(type);
      else state.filterTypes.add(type);
      chip.classList.toggle("on", state.filterTypes.has(type));
      renderGrid();
    });
    box.appendChild(chip);
  }
}

function visibleDex() {
  const text = state.filterText.trim().toLowerCase();
  let list = state.dex.filter((s) => {
    if (text && !s.name.toLowerCase().includes(text)
        && !pad3(s.national).includes(text)) return false;
    if (state.availableOnly && !s.available) return false;
    if (state.shinyOnly && !s.shiny_count) return false;
    if (state.filterTypes.size
        && !s.types.some((t) => state.filterTypes.has(t))) return false;
    return true;
  });
  if (state.sort === "name") {
    list = [...list].sort((a, b) => a.name.localeCompare(b.name));
  }
  return list;
}

function spriteBox(national, name, large) {
  const box = el("div", "sprite-box" + (large ? " large" : ""));
  const fallback = el("div", "fallback", name[0] || "?");
  const img = document.createElement("img");
  img.loading = "lazy";
  img.alt = name;
  img.src = `/static/sprites/${pad3(national)}.png`;
  img.addEventListener("error", () => img.remove(), { once: true });
  box.append(fallback, img);
  return box;
}

function renderGrid() {
  const grid = $("#grid");
  grid.textContent = "";
  const fragment = document.createDocumentFragment();
  for (const s of visibleDex()) {
    const card = el("button", "card");
    card.dataset.national = s.national;
    card.classList.toggle("disabled", !s.available);
    card.append(spriteBox(s.national, s.name, false));
    card.append(el("span", "dex-no", `#${pad3(s.national)}`));
    card.append(el("span", "name", s.name));
    const badges = el("span", "type-badges");
    for (const t of s.types) badges.append(typeChip(t));
    card.append(badges);
    if (s.shiny_count) card.append(el("span", "shiny-star", "✦"));
    if (s.count) card.append(el("span", "count", `${s.count} PK3`));
    else card.append(el("span", "no-mon", "No PK3 available"));
    card.addEventListener("click", () => openSpecies(s.national));
    fragment.append(card);
  }
  grid.append(fragment);
  $("#grid-empty").classList.toggle("hidden", visibleDex().length > 0);
}

/* ---------- modal plumbing ---------- */
function openModal() { $("#modal-backdrop").classList.remove("hidden"); }
function closeModal() {
  $("#modal-backdrop").classList.add("hidden");
  $("#modal-body").textContent = "";
  state.currentSpecies = null;
  state.currentInstance = null;
}
function setModalTitle(title, sub) {
  const h = el("h2", null, title);
  const s = el("div", "sub", sub || "");
  const body = $("#modal-body");
  body.textContent = "";
  body.append(h, s);
  return body;
}

async function openSpecies(national) {
  const res = await fetch(`/api/species/${national}`);
  if (!res.ok) return;
  const sp = await res.json();
  state.currentSpecies = sp;
  const body = setModalTitle(sp.name, `#${pad3(sp.national)} · National Pokédex`);
  const head = el("div", "species-head");
  head.append(spriteBox(sp.national, sp.name, true));
  const meta = el("div", "meta");
  const badges = el("div", "type-badges");
  for (const t of sp.types) badges.append(typeChip(t));
  meta.append(badges);
  if (sp.abilities.length) {
    meta.append(el("div", "sub", `Abilities: ${sp.abilities.join(" / ")}`));
  }
  meta.append(el("div", "sub", `Base stats: HP ${sp.base_stats.hp} · Atk ${sp.base_stats.atk} · ` +
    `Def ${sp.base_stats.def} · SpA ${sp.base_stats.spa} · SpD ${sp.base_stats.spd} · Spe ${sp.base_stats.spe}`));
  head.append(meta);
  body.append(head);

  body.append(el("h3", null, `Library instances (${sp.instances.length})`));
  if (!sp.instances.length) {
    body.append(el("p", "muted", "No PK3/EK3 file of this species in the library."));
    openModal();
    return;
  }
  const table = el("table", "instances");
  const thead = document.createElement("thead");
  thead.innerHTML = "<tr><th>File</th><th>Format</th><th>Level</th><th>OT</th><th>Status</th><th></th></tr>";
  table.append(thead);
  const tbody = document.createElement("tbody");
  for (const inst of sp.instances) {
    const tr = el("tr");
    if (!inst.tradeable) tr.classList.add("bad");
    const fileCell = el("td");
    const nick = el("div", null, inst.nickname || "—");
    const path = el("div", "muted", inst.relpath);
    fileCell.append(nick, path);
    tr.append(fileCell);
    tr.append(el("td", null, inst.format === "ek3" ? "EK3" : "PK3"));
    tr.append(el("td", null, inst.level ?? "—"));
    tr.append(el("td", null, inst.ot_name || "—"));
    const statusCell = el("td");
    const tag = el("span", "tag");
    if (inst.status === "ok") { tag.textContent = "valid"; tag.classList.add("checksum-ok"); }
    else if (inst.error === "invalid_size") { tag.textContent = "invalid size"; tag.classList.add("error"); }
    else if (inst.error === "undecodable") { tag.textContent = "undecodable"; tag.classList.add("error"); }
    else { tag.textContent = "bad checksum"; tag.classList.add("checksum-bad"); }
    statusCell.append(tag);
    if (inst.shiny) { const s = el("span", "tag shiny", "✦ shiny"); statusCell.append(" ", s); }
    tr.append(statusCell);
    const actions = el("td", "actions");
    const detailsBtn = el("button", "btn ghost", "Details");
    detailsBtn.addEventListener("click", () => openInstance(inst.id));
    actions.append(detailsBtn);
    const sendBtn = el("button", "btn", "Send to Switch");
    sendBtn.disabled = !inst.tradeable || (state.trade && state.trade.active);
    sendBtn.addEventListener("click", () => startTrade(inst));
    actions.append(" ", sendBtn);
    tr.append(actions);
    tbody.append(tr);
  }
  table.append(tbody);
  body.append(table);
  openModal();
}

function kv(label, value) {
  const row = el("div", "kv");
  row.append(el("span", null, label), el("span", null, value));
  return row;
}

async function openInstance(id) {
  const res = await fetch(`/api/pokemon/${id}`);
  if (!res.ok) return;
  const m = await res.json();
  state.currentInstance = m;
  const body = setModalTitle(
    m.nickname || m.species_name,
    `${m.species_name} (#${pad3(m.species_national)}) · ${m.relpath}`);
  const head = el("div", "species-head");
  head.append(spriteBox(m.species_national, m.species_name, true));
  const meta = el("div", "meta");
  const badges = el("div", "type-badges");
  for (const t of m.types || []) badges.append(typeChip(t));
  meta.append(badges);
  if (m.shiny) meta.append(el("div", "sub", "✦ Shiny"));
  meta.append(el("div", "sub", `${m.format.toUpperCase()} · ${m.size} bytes (${m.variant})`));
  head.append(meta);
  body.append(head);

  const grid = el("div", "detail-grid");
  grid.append(kv("Species", `${m.species_name} (#${m.species_national})`));
  grid.append(kv("Level", m.level != null ? String(m.level) : "—"));
  grid.append(kv("EXP", String(m.exp)));
  grid.append(kv("Held item", m.held_item.name));
  grid.append(kv("OT", `${m.ot_name} (TID ${m.tid} / SID ${m.sid})`));
  grid.append(kv("PID", m.pid.toString(16).padStart(8, "0")));
  grid.append(kv("Nature", m.nature_name));
  grid.append(kv("Gender", m.gender));
  grid.append(kv("Ability", `${m.ability_name} (slot ${m.ability_slot})`));
  grid.append(kv("Friendship", String(m.friendship)));
  grid.append(kv("Language", m.language.name));
  grid.append(kv("Origin game", m.met_game.name));
  grid.append(kv("Met location", m.met_location.name));
  grid.append(kv("Met level", m.met_level ? String(m.met_level) : "—"));
  grid.append(kv("Ball", m.ball.name));
  grid.append(kv("Pokérus", m.pokerus.infected
    ? `yes (strain ${m.pokerus.strain}, ${m.pokerus.days}d)` : "none"));
  grid.append(kv("Checksum", m.checksum_ok
    ? "valid" : `FAILED (${m.checksum.stored.toString(16)} != ${m.checksum.calc.toString(16)})`));
  body.append(grid);

  const moves = el("div", "sub");
  const moveText = m.moves.map((mv) => mv.name).join(" · ");
  moves.append(el("b", null, "Moves: "), moveText);
  body.append(moves);

  body.append(el("h3", null, "IVs"));
  const ivs = el("div", "ivs");
  for (const [label, iv] of [["HP", m.ivs[0]], ["Atk", m.ivs[1]], ["Def", m.ivs[2]],
                              ["SpA", m.ivs[3]], ["SpD", m.ivs[4]], ["Spe", m.ivs[5]]]) {
    const box = el("div", "iv");
    box.append(el("b", null, String(iv)), label);
    ivs.append(box);
  }
  body.append(ivs);

  const sendBtn = el("button", "btn", "Send to Switch");
  sendBtn.disabled = !m.tradeable || (state.trade && state.trade.active);
  sendBtn.addEventListener("click", () => startTrade({ id: m.id, ...m }));
  body.append(el("div", null), sendBtn);
  openModal();
}

/* ---------- trades ---------- */
async function startTrade(inst) {
  if (state.trade && state.trade.active) return;
  const label = `${inst.nickname || inst.species_name} (Lv ${inst.level ?? "?"})`;
  if (!confirm(`Send ${label} to the Switch?\n\nThe Switch must be in the Direct Corner trade room, waiting as the Leader.`)) return;
  const res = await fetch("/api/trade", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: inst.id }),
  });
  if (res.status === 409) { alert("A trade is already running."); return; }
  if (!res.ok) {
    alert(`Could not start the trade (${res.status}).`);
    return;
  }
  $("#log").textContent = "";
  applyState(await res.json());
}

async function cancelTrade() {
  const res = await fetch("/api/trade/cancel", { method: "POST" });
  applyState(await res.json());
}

function applyState(payload) {
  state.trade = payload;
  const badge = $("#state-badge");
  badge.textContent = payload.state;
  badge.className = `badge state-${payload.state}`;
  const steps = document.querySelectorAll(".step");
  const order = ["IDLE", "SCANNING", "JOINING", "CONNECTED", "TRADING", "COMPLETED"];
  const idx = order.indexOf(payload.state);
  for (const step of steps) {
    step.classList.remove("active", "done", "bad");
    const stepIdx = order.indexOf(step.dataset.step);
    if (payload.state === "FAILED" || payload.state === "CANCELLED") {
      if (step.dataset.step === payload.state) step.classList.add("bad");
    } else if (stepIdx >= 0 && idx >= 0) {
      if (stepIdx === idx) step.classList.add("active");
      else if (stepIdx < idx) step.classList.add("done");
    }
  }
  $("#btn-cancel").disabled = !payload.active;
  const current = $("#trade-current");
  if (payload.current) {
    const c = payload.current;
    current.textContent = "";
    current.append(el("b", null, `Trading ${c.nickname || c.species_name} ` +
      `(Lv ${c.level ?? "?"})${c.shiny ? " ✦" : ""}`));
    current.append(el("div", "muted", payload.dry_run ? "dry-run mode" : `phy ${payload.phy}`));
  } else if (payload.state === "IDLE") {
    current.textContent = "No trade running.";
  }
  if (!payload.active) refreshDexCounts();
  refreshTradeButtons();
}

function refreshTradeButtons() {
  document.querySelectorAll(".btn").forEach((b) => { /* no-op: buttons are per-render */ });
  if (state.currentSpecies) openSpecies(state.currentSpecies.national);
  else if (state.currentInstance) openInstance(state.currentInstance.id);
}

async function refreshDexCounts() {
  const res = await fetch("/api/pokedex");
  const data = await res.json();
  state.dex = data.species;
  renderGrid();
  const stats = await (await fetch("/api/library")).json();
  $("#library-stats").textContent =
    `${stats.entries} Pokémon · ${stats.valid} valid · ${stats.invalid} invalid`;
}

/* ---------- SSE ---------- */
let sseSource = null;
let lastEventAt = Date.now();

function connectEvents() {
  openEvents();
  // Watchdog: during an active trade the UI must never go blind. If the SSE
  // stream has been silent for too long, force a reconnect; in any case, keep
  // polling /api/state so the badge stays truthful even if the stream died.
  setInterval(() => {
    const active = state.trade && state.trade.active;
    if (!active) return;
    if (Date.now() - lastEventAt > 20000) openEvents();
    fetch("/api/state")
      .then((r) => r.json())
      .then(applyState)
      .catch(() => { /* server hiccup; the next tick retries */ });
  }, 5000);
}

function openEvents() {
  if (sseSource) sseSource.close();
  const source = new EventSource("/api/events");
  const touch = () => { lastEventAt = Date.now(); };
  source.addEventListener("state", (e) => { touch(); applyState(JSON.parse(e.data)); });
  source.addEventListener("log", (e) => {
    touch();
    const evt = JSON.parse(e.data);
    const logBox = $("#log");
    const line = el("div", "line" + (evt.milestone ? " milestone" : ""), evt.text);
    if (/(error|fatal|abort|fail)/i.test(evt.text) && !evt.milestone) line.classList.add("error");
    logBox.append(line);
    logBox.parentElement.scrollTop = logBox.parentElement.scrollHeight;
  });
  source.addEventListener("received", async (e) => {
    touch();
    const evt = JSON.parse(e.data);
    const box = $("#trade-result");
    box.classList.remove("hidden");
    box.textContent = "";
    box.append(el("h3", null, "Trade completed — Pokémon received!"));
    const row = el("div", "row");
    const r = evt.received;
    row.append(spriteBox(r.species_national, r.species_name, false));
    const info = el("div");
    info.append(el("div", null, `${r.nickname || r.species_name} · ` +
      `${r.species_name} (#${pad3(r.species_national)}) · Lv ${r.level ?? "?"}${r.shiny ? " ✦" : ""}`));
    info.append(el("div", "muted", `saved as ${evt.file}`));
    const addBtn = el("button", "btn", "Add to library");
    addBtn.addEventListener("click", () => addToLibrary(evt.record_id, addBtn));
    info.append(el("div", null), addBtn);
    row.append(info);
    box.append(row);
    await refreshDexCounts();
  });
  source.onerror = () => { /* EventSource auto-reconnects (retry: 2000) */ };
  sseSource = source;
}

async function addToLibrary(recordId, btn) {
  const res = await fetch(`/api/history/${recordId}/add-to-library`, { method: "POST" });
  if (!res.ok) { alert("Could not add to library."); return; }
  const data = await res.json();
  btn.textContent = "Added ✓";
  btn.disabled = true;
  await refreshDexCounts();
}

/* ---------- history ---------- */
async function openHistory() {
  const res = await fetch("/api/history");
  const data = await res.json();
  const body = setModalTitle("Trade history", "received Pokémon, newest first");
  if (!data.records.length) {
    body.append(el("p", "muted", "No trades yet."));
    openModal();
    return;
  }
  for (const rec of data.records) {
    const item = el("div", "history-item");
    item.append(spriteBox(rec.species_national, rec.species_name, false));
    const info = el("div", "grow");
    info.append(el("div", null, `${rec.species_name} (#${pad3(rec.species_national)}) · ` +
      `Lv ${rec.level ?? "?"}${rec.shiny ? " ✦" : ""}${rec.dry_run ? " · dry-run" : ""}`));
    info.append(el("div", "muted", `${rec.ts} · ${rec.file}`));
    item.append(info);
    const addBtn = el("button", "btn", "Add to library");
    addBtn.addEventListener("click", async () => {
      const r = await fetch(`/api/history/${rec.id}/add-to-library`, { method: "POST" });
      if (r.ok) { addBtn.textContent = "Added ✓"; addBtn.disabled = true; refreshDexCounts(); }
      else alert("Could not add to library.");
    });
    item.append(addBtn);
    body.append(item);
  }
  openModal();
}

/* ---------- wiring ---------- */
function wire() {
  $("#search").addEventListener("input", (e) => {
    state.filterText = e.target.value;
    renderGrid();
  });
  $("#available-only").addEventListener("change", (e) => {
    state.availableOnly = e.target.checked;
    renderGrid();
  });
  $("#shiny-only").addEventListener("change", (e) => {
    state.shinyOnly = e.target.checked;
    renderGrid();
  });
  $("#sort").addEventListener("change", (e) => {
    state.sort = e.target.value;
    renderGrid();
  });
  $("#btn-rescan").addEventListener("click", async () => {
    $("#btn-rescan").disabled = true;
    await fetch("/api/library/rescan", { method: "POST" });
    $("#btn-rescan").disabled = false;
    await refreshDexCounts();
  });
  $("#btn-history").addEventListener("click", openHistory);
  $("#btn-cancel").addEventListener("click", cancelTrade);
  $("#modal-close").addEventListener("click", closeModal);
  $("#modal-backdrop").addEventListener("click", (e) => {
    if (e.target === $("#modal-backdrop")) closeModal();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeModal();
  });
  $("#btn-panel").addEventListener("click", () => {
    document.body.classList.toggle("panel-open");
  });
}

async function init() {
  wire();
  connectEvents();
  const [trade] = await Promise.all([
    fetch("/api/state").then((r) => r.json()),
    loadDex(),
  ]);
  applyState(trade);
}

init();
