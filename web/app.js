/* ============================================================================
   app.js — TTRPG Grid Map Generator SPA controller.
   Vanilla ES module. Consumes Contract 4. Production path is mock-free; the
   dev mock is loaded ONLY when the URL carries ?mock=1.

   SECURITY NOTE: Every `.innerHTML =` / html: assignment in this file is fed
   either (a) a static, developer-authored SVG icon string from ICON, or
   (b) values passed through esc() (HTML-entity encoding). No unescaped
   user/server text ever reaches innerHTML. textContent is used for all raw
   dynamic strings.
   ============================================================================ */

import { Api } from "./api.js";

/* ---- optional dev mock (never referenced by index.html) --------------------
   When ?mock=1 is present we dynamically import dev-mock.js, which monkeypatches
   window.fetch with canned Contract-4 responses so the UI can be exercised
   without the Flask backend. Nothing in the production path touches this. */
const IS_MOCK = new URLSearchParams(location.search).has("mock");
if (IS_MOCK) {
  await import("./dev-mock.js").then((m) => m.installMock && m.installMock());
}

/* ============================ tiny DOM helpers ============================ */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const el = (tag, attrs = {}, ...kids) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") n.className = v;
    else if (k === "html") n.innerHTML = v;
    else if (k === "text") n.textContent = v;
    else if (k.startsWith("on") && typeof v === "function") n.addEventListener(k.slice(2), v);
    else if (k === "dataset") Object.assign(n.dataset, v);
    else n.setAttribute(k, v);
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    n.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return n;
};
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* SVG icon set (static, developer-authored — safe for innerHTML) */
const ICON = {
  download: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5"/><path d="M12 15V3"/></svg>`,
  copy: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>`,
  check: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>`,
  star: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="m12 2 3 6.3 6.9 1-5 4.9 1.2 6.8-6.1-3.2-6.1 3.2L7.1 14.2l-5-4.9 6.9-1Z"/></svg>`,
  starFill: `<svg viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="1" stroke-linejoin="round"><path d="m12 2 3 6.3 6.9 1-5 4.9 1.2 6.8-6.1-3.2-6.1 3.2L7.1 14.2l-5-4.9 6.9-1Z"/></svg>`,
  trash: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2m2 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/></svg>`,
  warn: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/><path d="M12 9v4M12 17h.01"/></svg>`,
  close: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg>`,
  ok: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.1V12a10 10 0 1 1-5.9-9.1"/><path d="M22 4 12 14.1l-3-3"/></svg>`,
  info: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/></svg>`,
  print: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9V2h12v7"/><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/><rect x="6" y="14" width="12" height="8" rx="1"/></svg>`,
  module: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M4 4h16v16H4z"/><path d="M4 9h16M9 4v16"/></svg>`,
  editPencil: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>`,
  revert: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7v6h6"/><path d="M3 13a9 9 0 1 0 3-7.7L3 8"/></svg>`,
};

/* ============================ global state ============================ */
const state = {
  view: "generate",
  palette: null,
  paletteByTerrain: {},
  paletteByProp: {},
  colorblind: false,
  floors: [],
  activeFloor: 0,
  layout: null,
  gridMode: "gridless",
  editMode: false,
  libMaps: [],
  libFilters: { search: "", biome: "", sort: "newest", favorite: false },
  selecting: false,
  selected: new Set(),
};

/* ============================ toasts ============================ */
function toast(msg, kind = "info", ms = 3200) {
  const icon = { ok: ICON.ok, err: ICON.warn, info: ICON.info }[kind] || ICON.info;
  const t = el("div", { class: `toast toast--${kind}`, html: icon });
  t.append(el("span", { text: msg }));
  $("#toasts").append(t);
  setTimeout(() => { t.style.opacity = "0"; t.style.transform = "translateX(20px)"; t.style.transition = "all .2s"; setTimeout(() => t.remove(), 200); }, ms);
}

/* ============================ formatting ============================ */
function fmtBytes(b) {
  if (b == null) return "—";
  if (b < 1024) return b + " B";
  const kb = b / 1024;
  if (kb < 1024) return kb.toFixed(0) + " KB";
  const mb = kb / 1024;
  if (mb < 1024) return mb.toFixed(mb < 10 ? 1 : 0) + " MB";
  return (mb / 1024).toFixed(2) + " GB";
}
function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }) +
    " · " + d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}
function densityLabel(v) {
  if (v <= 15) return "Very sparse";
  if (v <= 40) return "Sparse";
  if (v <= 60) return "Balanced";
  if (v <= 85) return "Dense";
  return "Very dense";
}
function titleCase(s) { return String(s).replace(/[_-]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()); }
function slugify(s) { return String(s || "map").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 60) || "map"; }

async function copyText(text, btn) {
  try {
    await navigator.clipboard.writeText(text);
    if (btn) { btn.classList.add("copy-ok"); const old = btn.innerHTML; btn.innerHTML = ICON.check; setTimeout(() => { btn.classList.remove("copy-ok"); btn.innerHTML = old; }, 1100); }
    toast("Copied to clipboard", "ok", 1600);
  } catch { toast("Copy failed — select and copy manually", "err"); }
}

function download(url, filename) {
  const a = el("a", { href: url, download: filename || "" });
  document.body.append(a); a.click(); a.remove();
}

/* ============================ view routing ============================ */
function setView(v) {
  state.view = v;
  $$(".nav__item").forEach((b) => b.classList.toggle("is-active", b.dataset.nav === v));
  $$(".view").forEach((s) => s.classList.toggle("is-active", s.id === "view-" + v));
  if (v === "library") loadLibrary();
  window.scrollTo(0, 0);
}

/* ============================ GENERATE: controls ============================ */
const SIZE_PRESETS = {
  small: { cols: 20, rows: 20 },
  standard: { cols: 30, rows: 20 },
  large: { cols: 60, rows: 60 },
};
function clampInt(v, lo, hi, dflt) {
  const n = parseInt(v, 10);
  if (isNaN(n)) return dflt;
  return Math.max(lo, Math.min(hi, n));
}
function currentDims() {
  const preset = $("#size-preset").value;
  if (preset === "custom") return { cols: clampInt($("#cols").value, 5, 400, 30), rows: clampInt($("#rows").value, 5, 400, 20) };
  return SIZE_PRESETS[preset] || SIZE_PRESETS.standard;
}
function selectedBiomes() { return $$("#biome-picker .chip.is-on").map((c) => c.dataset.biome); }

function buildSpec() {
  const { cols, rows } = currentDims();
  const seedRaw = $("#seed").value.trim();
  const seed = seedRaw === "" ? 0 : (parseInt(seedRaw, 10) || 0);
  const multi = $("#multi-level").checked;
  return {
    prompt: $("#prompt").value.trim(),
    cols, rows,
    px_per_square: clampInt($("#px").value, 40, 400, 140),
    feet_per_square: clampInt($("#feet").value, 1, 500, 5),
    seed,
    density: clampInt($("#density").value, 0, 100, 50) / 100,
    mood: $("#mood").value,
    biomes: selectedBiomes(),
    structure: null,
    multi_level: multi,
    levels: multi ? clampInt($("#levels").value, 2, 12, 2) : 1,
    palette_mode: state.colorblind ? "colorblind" : "standard",
    title: "",
  };
}

/* live estimate — debounced */
let estTimer = null;
function scheduleEstimate() { clearTimeout(estTimer); estTimer = setTimeout(runEstimate, 260); }
async function runEstimate() {
  const { cols, rows } = currentDims();
  const px = clampInt($("#px").value, 40, 400, 140);
  try {
    const est = await Api.estimate(cols, rows, px);
    $("#est-res").textContent = `${est.width}×${est.height}`;
    $("#est-mp").textContent = est.megapixels.toFixed(1) + " MP";
    $("#est-size").textContent = fmtBytes(est.est_bytes);
    renderPreWarnings(est.warnings || []);
  } catch (e) {
    const w = cols * px, h = rows * px, mp = (w * h) / 1e6;
    $("#est-res").textContent = `${w}×${h}`;
    $("#est-mp").textContent = mp.toFixed(1) + " MP";
    $("#est-size").textContent = "~" + fmtBytes(w * h * 0.9);
    renderPreWarnings(mp > 50 ? [`Large map (${mp.toFixed(0)} MP) — may exceed VTT limits.`] : []);
  }
}
function renderPreWarnings(warnings) {
  const box = $("#pre-warnings");
  box.textContent = "";
  for (const w of warnings) {
    const isErr = /exceed|too large|over|cap/i.test(w);
    const node = el("div", { class: "warn" + (isErr ? " warn--err" : ""), html: ICON.warn });
    node.append(el("span", { text: w }));
    box.append(node);
  }
}

/* biome tag picker */
function renderBiomePicker() {
  const box = $("#biome-picker");
  box.textContent = "";
  const biomes = (state.palette && state.palette.biomes) || DEFAULT_BIOMES;
  for (const b of biomes) {
    box.append(el("button", {
      class: "chip", dataset: { biome: b.id || b }, text: b.display || titleCase(b.id || b),
      onclick(e) { e.currentTarget.classList.toggle("is-on"); },
    }));
  }
}
const DEFAULT_BIOMES = [
  "forest", "jungle", "grassland", "desert", "swamp", "arctic", "mountain", "cave",
  "grotto", "coast", "sea", "river", "volcanic", "dungeon", "ruins", "village",
  "tavern", "temple", "crypt", "sewer", "mine", "market", "ship", "camp", "fey",
].map((id) => ({ id, display: titleCase(id) }));

/* ============================ GENERATE: run ============================ */
let generating = false;
async function doGenerate(forceReroll = false) {
  if (generating) return;
  const spec = buildSpec();
  if (forceReroll || spec.seed === 0) { spec.seed = Math.floor(Math.random() * 2 ** 31); $("#seed").value = spec.seed; }
  generating = true;
  showResultRegion();
  setGenLoading(true, "Parsing prompt…");
  const btn = $("#btn-generate");
  const old = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner"></span><span>Generating…</span>`;
  $("#btn-reroll").disabled = true;
  try {
    setTimeout(() => generating && setGenLoading(true, "Laying out grid…"), 350);
    setTimeout(() => generating && setGenLoading(true, "Assembling terrain & props…"), 900);
    const res = await Api.generate(spec);
    const maps = res.maps || (res.map ? [res.map] : []);
    if (!maps.length) throw new Error("Server returned no map.");
    await presentResult(maps, 0);
    toast("Map generated & saved to Library", "ok");
    refreshStats();
  } catch (e) {
    setGenLoading(false);
    toast("Generation failed: " + e.message, "err", 5000);
  } finally {
    generating = false;
    btn.disabled = false;
    btn.innerHTML = old;
    $("#btn-reroll").disabled = false;
  }
}
function showResultRegion() {
  $("#result-region").style.display = "";
  $("#result-region").scrollIntoView({ behavior: "smooth", block: "start" });
}
function setGenLoading(on, msg) {
  $("#gen-overlay").classList.toggle("is-on", on);
  if (msg) $("#gen-status").textContent = msg;
}

async function presentResult(floors, floorIdx) {
  state.floors = floors;
  state.activeFloor = floorIdx;
  const rec = floors[floorIdx];
  if (rec.seed != null) $("#seed").value = rec.seed;
  let layout = null;
  try { const full = await Api.getMap(rec.id); layout = full.layout; if (full.map) floors[floorIdx] = full.map; }
  catch { /* layout optional for viewing */ }
  state.layout = layout;
  renderFloorSwitch();
  setGridMode(state.gridMode, true);
  setGenLoading(false);
  renderRail(floors[floorIdx], layout);
  $("#result-title").textContent = floors[floorIdx].title || "Result";
  if (state.editMode) toggleEditMode(false);
}

function renderFloorSwitch() {
  const sw = $("#floor-switch");
  if (state.floors.length <= 1) { sw.style.display = "none"; sw.textContent = ""; return; }
  sw.style.display = "";
  sw.textContent = "";
  state.floors.forEach((f, i) => {
    sw.append(el("button", { class: i === state.activeFloor ? "is-on" : "",
      text: "Floor " + (f.floor_index != null ? f.floor_index + 1 : i + 1), onclick: () => switchFloor(i) }));
  });
}
async function switchFloor(i) {
  if (i === state.activeFloor) return;
  setGenLoading(true, "Loading floor…");
  await presentResult(state.floors, i);
}

function setGridMode(mode, force) {
  if (!force && mode === state.gridMode) return;
  state.gridMode = mode;
  $$("#grid-toggle button").forEach((b) => b.classList.toggle("is-on", b.dataset.grid === mode));
  const rec = state.floors[state.activeFloor];
  if (!rec) return;
  $("#preview-img").src = Api.fileUrl(rec.id, mode === "gridded" ? "gridded" : "gridless") + "?t=" + (rec._v || 0);
}

/* ============================ RESULT RAIL ============================ */
function renderRail(rec, layout) {
  const rail = $("#result-rail");
  rail.textContent = "";
  if (!rec) return;
  const px = rec.px_per_square, w = rec.width_px, h = rec.height_px, feet = rec.feet_per_square;
  const slug = slugify(rec.title || rec.prompt);

  const dl = el("div", { class: "panel" },
    el("div", { class: "panel__head", html: "<h3>Download</h3>" }),
    el("div", { class: "panel__body" },
      el("div", { class: "dl-grid" },
        dlBtn("Gridless PNG", () => download(Api.fileUrl(rec.id, "gridless"), `${slug}_${rec.cols}x${rec.rows}.png`)),
        dlBtn("Gridded PNG", () => download(Api.fileUrl(rec.id, "gridded"), `${slug}_${rec.cols}x${rec.rows}_gridded.png`)),
        dlBtn("Metadata JSON", () => download(Api.fileUrl(rec.id, "metadata"), `${slug}_${rec.cols}x${rec.rows}.json`)),
        dlBtn("Thumbnail", () => download(Api.fileUrl(rec.id, "thumb"), `${slug}_thumb.jpg`)),
      ),
      el("hr", { class: "divider" }),
      el("div", { class: "label", style: "margin-bottom:9px;", text: "More exports" }),
      el("div", { class: "export-links" },
        el("div", { class: "export-row" },
          exBtn(ICON.print, "Print PDF · Letter", () => download(Api.printPdfUrl(rec.id, "letter"), `${slug}_letter.pdf`)),
          exBtn(ICON.print, "A4", () => download(Api.printPdfUrl(rec.id, "a4"), `${slug}_a4.pdf`)),
        ),
        exBtn(ICON.module, "Foundry module", () => download(Api.foundryModuleUrl([rec.id], (rec.title || rec.prompt || "maps").slice(0, 40)), `${slug}-module.zip`)),
      ),
    ));
  rail.append(dl);

  const foundryLine = `Foundry: Grid Size = ${px}px, Scene Width = ${w}, Scene Height = ${h}, Grid Type = Square`;
  const owlbearLine = `Owlbear: Grid Size = ${px}px, Grid Type = Square, Cell Size = ${feet} ft`;
  rail.append(el("div", { class: "panel" },
    el("div", { class: "panel__head", html: "<h3>Ready-to-paste setup</h3>" }),
    el("div", { class: "panel__body setup-block" }, setupLine("Foundry VTT", foundryLine), setupLine("Owlbear Rodeo", owlbearLine))));

  if (rec.warnings && rec.warnings.length) {
    const wbox = el("div", { class: "panel" }, el("div", { class: "panel__head", html: "<h3>Warnings</h3>" }),
      el("div", { class: "panel__body warnings" }));
    rec.warnings.forEach((wm) => { const node = el("div", { class: "warn", html: ICON.warn }); node.append(el("span", { text: wm })); wbox.querySelector(".warnings").append(node); });
    rail.append(wbox);
  }

  const gi = layout ? layout.indoor === false : undefined;
  rail.append(el("div", { class: "panel" }, el("div", { class: "panel__head", html: "<h3>Details</h3>" }),
    el("div", { class: "panel__body" }, metaTable([
      ["Grid", `${rec.cols} × ${rec.rows} squares`],
      ["Scale", `${feet} ft/square · ${px} px/square`],
      ["Resolution", `${w} × ${h} px`],
      ["Seed", String(rec.seed)],
      ["Mood", titleCase(rec.mood || "neutral")],
      ["Density", (Math.round((rec.density ?? 0.5) * 100)) + "%"],
      ["Biomes", (rec.biomes || []).map(titleCase).join(", ") || "—"],
      layout ? ["Global illum.", gi ? "On (outdoor)" : "Off (indoor)"] : null,
      ["File size", fmtBytes(rec.bytes)],
    ].filter(Boolean)))));
}
function dlBtn(label, onclick) {
  const b = el("button", { class: "btn btn--block", onclick, html: ICON.download });
  b.append(el("span", { text: label })); return b;
}
function exBtn(icon, label, onclick) {
  const b = el("button", { class: "btn btn--sm", onclick, html: icon });
  b.append(el("span", { text: label })); return b;
}
function setupLine(k, v) {
  const line = el("div", { class: "setup-line" });
  line.append(el("div", { class: "setup-line__body" }, el("div", { class: "setup-line__k", text: k }), el("div", { class: "setup-line__v", text: v })));
  line.append(el("button", { class: "btn btn--icon btn--sm", title: "Copy", html: ICON.copy, onclick: (e) => copyText(v, e.currentTarget) }));
  return line;
}
function metaTable(rows) {
  const t = el("table", { class: "meta-table" });
  for (const [k, v] of rows) t.append(el("tr", {}, el("td", { text: k }), el("td", { text: v })));
  return t;
}

/* ============================ MANUAL EDITING (result view) ============================ */
function toggleEditMode(on) {
  if (on === undefined) on = !state.editMode;
  state.editMode = on;
  $("#preview").classList.toggle("is-editing", on);
  const btn = $("#btn-edit-mode");
  btn.classList.toggle("btn--primary", on);
  btn.innerHTML = on ? ICON.check + "<span>Done editing</span>" : ICON.editPencil + "<span>Edit</span>";
  closeFlyout();
  if (on) { setGridMode("gridded"); ensureEditRevertBtn(true); toast("Edit mode: click a square to swap terrain, or a prop to modify.", "info", 4000); }
  else ensureEditRevertBtn(false);
}
function ensureEditRevertBtn(on) {
  const bar = $("#preview .preview__toolbar");
  let rb = $("#btn-revert");
  if (on && !rb) {
    rb = el("button", { id: "btn-revert", class: "btn btn--sm btn--danger", title: "Revert to the last generated state", html: ICON.revert + "<span>Revert</span>", onclick: doRevert });
    bar.insertBefore(rb, $("#btn-edit-mode"));
  } else if (!on && rb) rb.remove();
}
function onEditClick(ev) {
  if (!state.editMode || !state.layout) return;
  const img = $("#preview-img");
  const r = img.getBoundingClientRect();
  const rec = state.floors[state.activeFloor];
  const gx = Math.floor(((ev.clientX - r.left) / r.width) * rec.cols);
  const gy = Math.floor(((ev.clientY - r.top) / r.height) * rec.rows);
  if (gx < 0 || gy < 0 || gx >= rec.cols || gy >= rec.rows) return;
  const prop = propAt(gx, gy);
  if (prop) openPropFlyout(prop, ev);
  else openTerrainFlyout(gx, gy, ev);
}
function propAt(gx, gy) {
  const props = (state.layout && state.layout.props) || [];
  for (const p of props) {
    const def = state.paletteByProp[p.type] || {};
    let [fw, fh] = def.footprint || [1, 1];
    if (p.rot === 90 || p.rot === 270) [fw, fh] = [fh, fw];
    if (gx >= p.x && gx < p.x + fw && gy >= p.y && gy < p.y + fh) return p;
  }
  return null;
}
function placeFlyout(fly, ev, stageSel) {
  const stage = $(stageSel);
  fly.style.left = "0px"; fly.style.top = "0px";
  stage.append(fly);
  const sr = stage.getBoundingClientRect();
  const fr = fly.getBoundingClientRect();
  let x = ev.clientX - sr.left + 8, y = ev.clientY - sr.top + 8;
  if (x + fr.width > sr.width) x = Math.max(4, sr.width - fr.width - 4);
  if (y + fr.height > sr.height) y = Math.max(4, sr.height - fr.height - 4);
  fly.style.left = x + "px"; fly.style.top = y + "px";
}
function closeFlyout() { $$(".edit-flyout").forEach((f) => f.remove()); }

function terrainSwatch(t, cur, onpick) {
  const tid = t.id || t;
  const preview = t.preview || (t.tiles && t.tiles[0]);
  return el("button", { class: "tile-swatch" + (tid === cur ? " is-on" : ""), title: t.display || titleCase(tid),
    text: (t.display || titleCase(tid)).slice(0, 8), style: preview ? `background-image:url(${esc(preview)})` : "", onclick: () => onpick(tid) });
}
function openTerrainFlyout(gx, gy, ev) {
  closeFlyout();
  const cur = state.layout.cells?.[gy]?.[gx];
  const fly = el("div", { class: "edit-flyout" }, el("h4", { text: `Swap terrain · (${gx}, ${gy})` }), el("div", { class: "sub", text: "Current: " + titleCase(cur || "unknown") }));
  const grid = el("div", { class: "tile-grid" });
  for (const t of ((state.palette && state.palette.terrains) || [])) grid.append(terrainSwatch(t, cur, (tid) => applyTerrain(gx, gy, tid)));
  fly.append(grid);
  placeFlyout(fly, ev, "#preview-stage");
}
async function applyTerrain(gx, gy, terrain) { closeFlyout(); await runEdit({ set_cells: [{ x: gx, y: gy, terrain }] }, `Terrain → ${titleCase(terrain)}`); }

function openPropFlyout(prop, ev) {
  closeFlyout();
  const def = state.paletteByProp[prop.type] || {};
  const fly = el("div", { class: "edit-flyout" }, el("h4", { text: def.display || titleCase(prop.type) }), el("div", { class: "sub", text: `at (${prop.x}, ${prop.y}) · rot ${prop.rot || 0}°` }));
  const actions = el("div", { class: "flyout-actions" });
  if (def.rotatable !== false) actions.append(el("button", { class: "btn btn--sm", text: "Rotate 90°", onclick: () => runEdit({ props_update: [{ id: prop.id, rot: ((prop.rot || 0) + 90) % 360 }] }, "Prop rotated") }));
  actions.append(el("button", { class: "btn btn--sm btn--danger", html: ICON.trash + "<span>Remove</span>", onclick: () => runEdit({ props_remove: [prop.id] }, "Prop removed") }));
  fly.append(actions);
  const compat = ((state.palette && state.palette.props) || []).filter((p) => (p.category === def.category) && (p.type || p.id) !== prop.type);
  if (compat.length) {
    fly.append(el("div", { class: "sub", style: "margin:10px 0 6px;", text: "Replace with:" }));
    const grid = el("div", { class: "tile-grid" });
    for (const p of compat.slice(0, 12)) {
      const ptype = p.type || p.id; const preview = p.preview || (p.variants && p.variants[0]);
      grid.append(el("button", { class: "tile-swatch", title: p.display || titleCase(ptype), text: (p.display || titleCase(ptype)).slice(0, 7),
        style: preview ? `background-image:url(${esc(preview)})` : "", onclick: () => runEdit({ props_update: [{ id: prop.id, type: ptype }] }, "Prop replaced") }));
    }
    fly.append(grid);
  }
  placeFlyout(fly, ev, "#preview-stage");
}
async function runEdit(patch, okMsg) {
  closeFlyout();
  const rec = state.floors[state.activeFloor];
  try { const res = await Api.editMap(rec.id, patch); applyEditResult(res); toast(okMsg || "Edit applied", "ok", 1800); }
  catch (e) { toast("Edit failed: " + e.message, "err"); }
}
async function doRevert() {
  const rec = state.floors[state.activeFloor];
  try { const res = await Api.revertMap(rec.id); applyEditResult(res); toast("Reverted to generated state", "ok"); }
  catch (e) { toast("Revert failed: " + e.message, "err"); }
}
function applyEditResult(res) {
  if (res.layout) state.layout = res.layout;
  if (res.map) state.floors[state.activeFloor] = res.map;
  const updated = state.floors[state.activeFloor];
  updated._v = (updated._v || 0) + 1 + (Date.now() % 100000);
  setGridMode(state.gridMode, true);
  renderRail(updated, state.layout);
}

/* ============================ LIBRARY ============================ */
async function loadLibrary() {
  const grid = $("#lib-grid");
  grid.innerHTML = `<div class="empty-state"><span class="spinner spinner--lg"></span></div>`;
  $("#lib-empty").style.display = "none";
  try {
    const data = await Api.listMaps({
      search: state.libFilters.search, biome: state.libFilters.biome, sort: state.libFilters.sort,
      favorite: state.libFilters.favorite ? 1 : "",
    });
    state.libMaps = data.maps || [];
    updateStorageHeader(data.total_count, data.total_bytes);
    renderLibraryGrid();
    refreshStats();
  } catch (e) {
    grid.textContent = "";
    const box = el("div", { class: "empty-state", html: ICON.warn });
    box.append(el("h3", { text: "Couldn't load library" }), el("p", { text: e.message }));
    grid.append(box);
  }
}
function renderLibraryGrid() {
  const grid = $("#lib-grid");
  grid.textContent = "";
  const maps = groupSets(state.libMaps);
  if (!maps.length) { $("#lib-empty").style.display = ""; return; }
  $("#lib-empty").style.display = "none";
  for (const m of maps) grid.append(mapCard(m));
}
function groupSets(maps) {
  const seenSet = new Set(); const out = [];
  for (const m of maps) {
    if (m.set_id) {
      if (seenSet.has(m.set_id)) continue;
      seenSet.add(m.set_id);
      const floors = maps.filter((x) => x.set_id === m.set_id);
      const rep = floors.find((x) => (x.floor_index ?? 0) === 0) || floors[0];
      rep._setSize = floors.length;
      out.push(rep);
    } else out.push(m);
  }
  return out;
}
function mapCard(m) {
  const card = el("div", { class: "map-card", dataset: { id: m.id },
    onclick: () => { if (state.selecting) toggleSelect(m.id); else openDetail(m.id); } });
  const check = el("div", { class: "map-card__check" + (state.selected.has(m.id) ? " is-on" : ""), html: ICON.check,
    onclick: (e) => { e.stopPropagation(); toggleSelect(m.id); } });
  check.style.display = state.selecting ? "grid" : "none";
  const fav = el("div", { class: "map-card__fav" + (m.favorite ? " is-on" : ""), title: "Favorite",
    html: m.favorite ? ICON.starFill : ICON.star, onclick: (e) => { e.stopPropagation(); toggleFavorite(m); } });
  const thumb = el("div", { class: "map-card__thumb", style: `background-image:url(${esc(Api.thumbUrl(m))})` }, check, fav);
  if (m._setSize) thumb.append(el("div", { class: "map-card__set", text: `${m._setSize} floors` }));
  const tagsRow = ((m.tags && m.tags.length) || (m.biomes && m.biomes.length))
    ? el("div", { class: "map-card__tags" },
        ...(m.biomes || []).slice(0, 3).map((b) => el("span", { class: "tag-pill", text: titleCase(b) })),
        ...(m.tags || []).slice(0, 3).map((t) => el("span", { class: "tag-pill", text: t })))
    : null;
  card.append(thumb, el("div", { class: "map-card__body" },
    el("div", { class: "map-card__title", text: m.title || m.prompt || "Untitled map" }),
    el("div", { class: "map-card__meta" }, el("span", { text: `${m.cols}×${m.rows}, ${m.feet_per_square} ft/sq` }), el("span", { text: fmtDate(m.created_at) })),
    tagsRow));
  return card;
}
function updateStorageHeader(count, bytes) {
  $("#stat-count").textContent = count ?? state.libMaps.length;
  $("#stat-size").textContent = fmtBytes(bytes ?? 0);
}
async function toggleFavorite(m) {
  try {
    const res = await Api.patchMap(m.id, { favorite: !m.favorite });
    m.favorite = res.map ? res.map.favorite : !m.favorite;
    syncLibRecord(m.id, { favorite: m.favorite });
    renderLibraryGrid();
  } catch (e) { toast("Couldn't update favorite: " + e.message, "err"); }
}
function setSelecting(on) {
  state.selecting = on;
  if (!on) state.selected.clear();
  $("#lib-select-toggle").classList.toggle("is-on", on);
  $("#lib-select-toggle").textContent = on ? "Cancel" : "Select";
  $("#bulk-bar").classList.toggle("is-on", on);
  renderLibraryGrid();
  updateBulkCount();
}
function toggleSelect(id) {
  if (state.selected.has(id)) state.selected.delete(id); else state.selected.add(id);
  renderLibraryGrid();
  updateBulkCount();
}
function updateBulkCount() { $("#bulk-count").textContent = `${state.selected.size} selected`; }

/* ============================ DETAIL MODAL ============================ */
async function openDetail(id) {
  const modal = $("#detail-modal");
  modal.innerHTML = `<div class="modal__body" style="text-align:center;padding:60px;"><span class="spinner spinner--lg"></span></div>`;
  openOverlay("#detail-overlay");
  try {
    const { map, layout } = await Api.getMap(id);
    let floors = [map];
    if (map.set_id) {
      const all = await Api.listMaps({ set: map.set_id });
      floors = (all.maps || []).sort((a, b) => (a.floor_index ?? 0) - (b.floor_index ?? 0));
      if (!floors.length) floors = [map];
    }
    renderDetail(map, layout, floors);
  } catch (e) {
    modal.textContent = "";
    modal.append(el("div", { class: "modal__head", html: "<h2>Error</h2>" }),
      el("div", { class: "modal__body" }, el("p", { class: "confirm-text", text: e.message })),
      el("div", { class: "modal__foot" }, el("div", { class: "spacer" }), el("button", { class: "btn", text: "Close", onclick: () => closeOverlay("#detail-overlay") })));
  }
}

const detailState = { map: null, layout: null, floors: [], activeFloor: 0, grid: "gridless", edit: false };

function renderDetail(map, layout, floors) {
  detailState.map = map; detailState.layout = layout; detailState.floors = floors;
  detailState.activeFloor = Math.max(0, floors.findIndex((f) => f.id === map.id));
  detailState.grid = "gridless"; detailState.edit = false;
  const modal = $("#detail-modal");
  const slug = slugify(map.title || map.prompt);
  const px = map.px_per_square, w = map.width_px, h = map.height_px, feet = map.feet_per_square;
  modal.textContent = "";

  const titleEl = el("h2", { text: map.title || map.prompt || "Untitled map", title: "Click to rename", style: "cursor:text;", onclick: () => startRename(titleEl, map) });
  const favBtn = el("button", { class: "btn btn--icon", title: "Favorite", html: map.favorite ? ICON.starFill : ICON.star, style: map.favorite ? "color:var(--gold-bright)" : "",
    onclick: async () => { await toggleFavorite(map); favBtn.innerHTML = map.favorite ? ICON.starFill : ICON.star; favBtn.style.color = map.favorite ? "var(--gold-bright)" : ""; } });
  modal.append(el("div", { class: "modal__head" }, titleEl, el("div", { class: "spacer" }), favBtn,
    el("button", { class: "btn btn--icon", title: "Close", html: ICON.close, onclick: () => closeOverlay("#detail-overlay") })));

  const previewImg = el("img", { class: "preview__img", alt: "Map preview", src: Api.fileUrl(map.id, "gridless") });
  const gridSeg = el("div", { class: "seg" },
    el("button", { class: "is-on", dataset: { g: "gridless" }, text: "Gridless", onclick: () => setDetailGrid("gridless", previewImg) }),
    el("button", { dataset: { g: "gridded" }, text: "Gridded", onclick: () => setDetailGrid("gridded", previewImg) }));
  const toolbar = el("div", { class: "preview__toolbar" }, gridSeg, el("div", { class: "spacer" }),
    el("button", { class: "btn btn--sm", id: "detail-edit-btn", html: ICON.editPencil + "<span>Edit</span>", onclick: () => toggleDetailEdit(previewImg) }));
  if (floors.length > 1) {
    const floorSw = el("div", { class: "floor-switch" });
    floors.forEach((f, i) => floorSw.append(el("button", { class: i === detailState.activeFloor ? "is-on" : "", text: "Floor " + ((f.floor_index ?? i) + 1),
      onclick: () => { openDetail(f.id); } })));
    toolbar.prepend(floorSw);
  }
  const preview = el("div", { class: "preview", id: "detail-preview", style: "min-height:auto" }, toolbar,
    el("div", { class: "preview__stage", id: "detail-stage" }, previewImg, el("div", { class: "preview__overlay", id: "detail-overlay-canvas" })));

  const foundryLine = `Foundry: Grid Size = ${px}px, Scene Width = ${w}, Scene Height = ${h}, Grid Type = Square`;
  const owlbearLine = `Owlbear: Grid Size = ${px}px, Grid Type = Square, Cell Size = ${feet} ft`;
  const rail = el("div", { class: "rail" },
    el("div", { class: "panel" }, el("div", { class: "panel__head", html: "<h3>Download</h3>" }),
      el("div", { class: "panel__body" },
        el("div", { class: "dl-grid" },
          dlBtn("Gridless PNG", () => download(Api.fileUrl(map.id, "gridless"), `${slug}_${map.cols}x${map.rows}.png`)),
          dlBtn("Gridded PNG", () => download(Api.fileUrl(map.id, "gridded"), `${slug}_${map.cols}x${map.rows}_gridded.png`)),
          dlBtn("Metadata", () => download(Api.fileUrl(map.id, "metadata"), `${slug}_${map.cols}x${map.rows}.json`)),
          dlBtn("Thumbnail", () => download(Api.fileUrl(map.id, "thumb"), `${slug}_thumb.jpg`)),
        ),
        el("hr", { class: "divider" }),
        el("div", { class: "export-links" },
          el("div", { class: "export-row" }, exBtn(ICON.print, "PDF · Letter", () => download(Api.printPdfUrl(map.id, "letter"), `${slug}_letter.pdf`)), exBtn(ICON.print, "A4", () => download(Api.printPdfUrl(map.id, "a4"), `${slug}_a4.pdf`))),
          exBtn(ICON.module, "Foundry module", () => download(Api.foundryModuleUrl([map.id], map.title || slug), `${slug}-module.zip`))))),
    el("div", { class: "panel" }, el("div", { class: "panel__head", html: "<h3>Ready-to-paste setup</h3>" }),
      el("div", { class: "panel__body setup-block" }, setupLine("Foundry VTT", foundryLine), setupLine("Owlbear Rodeo", owlbearLine))),
    renderTagEditor(map),
    el("div", { class: "panel" }, el("div", { class: "panel__head", html: "<h3>Details</h3>" }),
      el("div", { class: "panel__body" }, metaTable([
        ["Prompt", map.prompt || "—"], ["Grid", `${map.cols} × ${map.rows}`], ["Scale", `${feet} ft/sq · ${px} px/sq`],
        ["Resolution", `${w} × ${h}`], ["Seed", String(map.seed)], ["Mood", titleCase(map.mood || "neutral")],
        ["Created", fmtDate(map.created_at)], ["Size", fmtBytes(map.bytes)],
      ]))));

  modal.append(el("div", { class: "modal__body" }, el("div", { class: "detail-grid" }, preview, rail)));
  modal.append(el("div", { class: "modal__foot" },
    el("button", { class: "btn btn--danger", html: ICON.trash + "<span>Delete map</span>", onclick: () => confirmDelete(map) }),
    el("div", { class: "spacer" }),
    el("button", { class: "btn", text: "Close", onclick: () => closeOverlay("#detail-overlay") })));

  if (!layout) Api.getMap(map.id).then((d) => { detailState.layout = d.layout; }).catch(() => {});
}

function setDetailGrid(mode, img) {
  detailState.grid = mode;
  $$("#detail-preview .seg button").forEach((b) => b.classList.toggle("is-on", b.dataset.g === mode));
  img.src = Api.fileUrl(detailState.map.id, mode) + "?t=" + Date.now();
}
function startRename(titleEl, map) {
  const cur = map.title || map.prompt || "";
  const input = el("input", { type: "text", value: cur, style: "font-family:var(--font-display);font-size:20px;" });
  const commit = async () => {
    const val = input.value.trim();
    titleEl.textContent = val || (map.prompt || "Untitled map");
    input.replaceWith(titleEl);
    if (val && val !== cur) { try { await Api.patchMap(map.id, { title: val }); map.title = val; syncLibRecord(map.id, { title: val }); toast("Renamed", "ok", 1600); } catch (e) { toast("Rename failed: " + e.message, "err"); } }
  };
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") commit(); if (e.key === "Escape") input.replaceWith(titleEl); });
  input.addEventListener("blur", commit);
  titleEl.replaceWith(input); input.focus(); input.select();
}
function renderTagEditor(map) {
  const body = el("div", { class: "panel__body" });
  const editor = el("div", { class: "tag-editor" });
  const rerender = () => {
    editor.textContent = "";
    (map.tags || []).forEach((tag) => {
      const chip = el("span", { class: "chip is-on" }, el("span", { text: tag }));
      chip.append(el("span", { class: "chip__x", text: "✕", style: "cursor:pointer", onclick: () => removeTag(map, tag, rerender) }));
      editor.append(chip);
    });
    editor.append(el("input", { type: "text", placeholder: "add tag…", onkeydown: (e) => { if (e.key === "Enter") { addTag(map, e.target.value.trim(), rerender); e.target.value = ""; } } }));
  };
  rerender();
  body.append(editor);
  return el("div", { class: "panel" }, el("div", { class: "panel__head", html: "<h3>Tags &amp; campaign</h3>" }), body);
}
async function addTag(map, tag, rerender) {
  if (!tag) return;
  const tags = [...new Set([...(map.tags || []), tag])];
  try { await Api.patchMap(map.id, { tags }); map.tags = tags; syncLibRecord(map.id, { tags }); rerender(); } catch (e) { toast("Couldn't add tag: " + e.message, "err"); }
}
async function removeTag(map, tag, rerender) {
  const tags = (map.tags || []).filter((t) => t !== tag);
  try { await Api.patchMap(map.id, { tags }); map.tags = tags; syncLibRecord(map.id, { tags }); rerender(); } catch (e) { toast("Couldn't remove tag: " + e.message, "err"); }
}
function syncLibRecord(id, fields) {
  const idx = state.libMaps.findIndex((x) => x.id === id);
  if (idx >= 0) Object.assign(state.libMaps[idx], fields);
}

function toggleDetailEdit(img) {
  detailState.edit = !detailState.edit;
  const preview = $("#detail-preview");
  preview.classList.toggle("is-editing", detailState.edit);
  const btn = $("#detail-edit-btn");
  btn.classList.toggle("btn--primary", detailState.edit);
  if (detailState.edit) {
    setDetailGrid("gridded", img);
    ensureDetailRevert(true, img);
    if (!detailState.layout) Api.getMap(detailState.map.id).then((d) => { detailState.layout = d.layout; });
    toast("Edit mode: click a square or prop.", "info", 3200);
  } else { ensureDetailRevert(false); $$("#detail-stage .edit-flyout").forEach((f) => f.remove()); }
  btn.innerHTML = detailState.edit ? ICON.check + "<span>Done</span>" : ICON.editPencil + "<span>Edit</span>";
}
function ensureDetailRevert(on, img) {
  const bar = $("#detail-preview .preview__toolbar");
  let rb = $("#detail-revert");
  if (on && !rb) {
    rb = el("button", { id: "detail-revert", class: "btn btn--sm btn--danger", text: "Revert",
      onclick: async () => { try { const r = await Api.revertMap(detailState.map.id); if (r.layout) detailState.layout = r.layout; if (r.map) detailState.map = r.map; img.src = Api.fileUrl(detailState.map.id, detailState.grid) + "?t=" + Date.now(); toast("Reverted", "ok"); } catch (e) { toast(e.message, "err"); } } });
    bar.insertBefore(rb, $("#detail-edit-btn"));
  } else if (!on && rb) rb.remove();
}
document.addEventListener("click", (ev) => {
  const canvas = ev.target.closest && ev.target.closest("#detail-overlay-canvas");
  if (!canvas || !detailState.edit || !detailState.layout) return;
  const img = $("#detail-stage img");
  const r = img.getBoundingClientRect();
  const map = detailState.map;
  const gx = Math.floor(((ev.clientX - r.left) / r.width) * map.cols);
  const gy = Math.floor(((ev.clientY - r.top) / r.height) * map.rows);
  if (gx < 0 || gy < 0 || gx >= map.cols || gy >= map.rows) return;
  openDetailFlyout(gx, gy, ev, img);
});
function openDetailFlyout(gx, gy, ev, img) {
  $$("#detail-stage .edit-flyout").forEach((f) => f.remove());
  const L = detailState.layout;
  let prop = null;
  for (const p of (L.props || [])) {
    const def = state.paletteByProp[p.type] || {}; let [fw, fh] = def.footprint || [1, 1];
    if (p.rot === 90 || p.rot === 270) [fw, fh] = [fh, fw];
    if (gx >= p.x && gx < p.x + fw && gy >= p.y && gy < p.y + fh) { prop = p; break; }
  }
  const doEdit = async (patch, msg) => {
    $$("#detail-stage .edit-flyout").forEach((f) => f.remove());
    try { const res = await Api.editMap(detailState.map.id, patch); if (res.layout) detailState.layout = res.layout; if (res.map) detailState.map = res.map;
      img.src = Api.fileUrl(detailState.map.id, detailState.grid) + "?t=" + Date.now(); toast(msg, "ok", 1600); } catch (e) { toast("Edit failed: " + e.message, "err"); }
  };
  let fly;
  if (prop) {
    const def = state.paletteByProp[prop.type] || {};
    fly = el("div", { class: "edit-flyout" }, el("h4", { text: def.display || titleCase(prop.type) }), el("div", { class: "sub", text: `(${prop.x}, ${prop.y}) · ${prop.rot || 0}°` }),
      el("div", { class: "flyout-actions" },
        def.rotatable !== false ? el("button", { class: "btn btn--sm", text: "Rotate 90°", onclick: () => doEdit({ props_update: [{ id: prop.id, rot: ((prop.rot || 0) + 90) % 360 }] }, "Rotated") }) : null,
        el("button", { class: "btn btn--sm btn--danger", html: ICON.trash + "<span>Remove</span>", onclick: () => doEdit({ props_remove: [prop.id] }, "Removed") })));
  } else {
    const cur = L.cells?.[gy]?.[gx];
    fly = el("div", { class: "edit-flyout" }, el("h4", { text: `Swap terrain · (${gx},${gy})` }), el("div", { class: "sub", text: "Current: " + titleCase(cur || "?") }));
    const grid = el("div", { class: "tile-grid" });
    for (const t of ((state.palette && state.palette.terrains) || [])) grid.append(terrainSwatch(t, cur, (tid) => doEdit({ set_cells: [{ x: gx, y: gy, terrain: tid }] }, "Terrain swapped")));
    fly.append(grid);
  }
  placeFlyout(fly, ev, "#detail-stage");
}

/* ============================ DELETE (confirm) ============================ */
let confirmAction = null;
function confirmDelete(map) {
  $("#confirm-title").textContent = "Delete map?";
  const box = $("#confirm-text");
  box.textContent = "Permanently delete ";
  box.append(el("b", { text: map.title || map.prompt || "this map" }), document.createTextNode(" and free its disk space? This cannot be undone."));
  $("#confirm-ok").textContent = "Delete";
  confirmAction = async () => {
    try {
      await Api.deleteMap(map.id);
      state.libMaps = state.libMaps.filter((x) => x.id !== map.id);
      state.selected.delete(map.id);
      closeOverlay("#confirm-overlay"); closeOverlay("#detail-overlay");
      renderLibraryGrid(); refreshStats();
      toast("Map deleted", "ok");
    } catch (e) { toast("Delete failed: " + e.message, "err"); }
  };
  openOverlay("#confirm-overlay");
}

/* ============================ STATS ============================ */
async function refreshStats() {
  try {
    const s = await Api.stats();
    $("#stat-count").textContent = s.map_count ?? 0;
    $("#stat-size").textContent = fmtBytes(s.total_bytes ?? 0);
    $("#nav-lib-count").textContent = s.map_count ?? 0;
    const side = $("#side-storage"); side.textContent = "";
    side.append(el("b", { text: String(s.map_count ?? 0) }), document.createTextNode(" maps · "), el("b", { text: fmtBytes(s.total_bytes ?? 0) }));
    const byB = s.by_biome || {};
    const entries = Object.entries(byB).sort((a, b) => b[1] - a[1]);
    $("#stat-biomes").textContent = entries.length ? entries.slice(0, 4).map(([b, n]) => `${titleCase(b)} ${n}`).join(" · ") : "";
    const sel = $("#lib-biome"); const cur = sel.value;
    sel.textContent = "";
    sel.append(el("option", { value: "", text: "All biomes" }));
    entries.forEach(([b, n]) => sel.append(el("option", { value: b, text: `${titleCase(b)} (${n})` })));
    sel.value = cur;
  } catch { /* stats non-critical */ }
}

/* ============================ overlay helpers ============================ */
function openOverlay(sel) { $(sel).classList.add("is-open"); document.body.style.overflow = "hidden"; }
function closeOverlay(sel) { $(sel).classList.remove("is-open"); if (!$$(".overlay.is-open").length) document.body.style.overflow = ""; }

/* ============================ colorblind mode ============================ */
function setColorblind(on) {
  state.colorblind = on;
  document.body.classList.toggle("cb-mode", on);
  try { localStorage.setItem("ttrpg_cb", on ? "1" : "0"); } catch {}
}

/* ============================ wiring ============================ */
function wire() {
  $$(".nav__item").forEach((b) => b.addEventListener("click", () => setView(b.dataset.nav)));
  $$("[data-example]").forEach((c) => c.addEventListener("click", () => { $("#prompt").value = c.dataset.example; $("#prompt").focus(); }));
  $("#size-preset").addEventListener("change", (e) => { $("#custom-dims").classList.toggle("is-on", e.target.value === "custom"); scheduleEstimate(); });
  ["#cols", "#rows", "#px"].forEach((s) => $(s).addEventListener("input", scheduleEstimate));
  $("#density").addEventListener("input", (e) => { $("#density-val").textContent = densityLabel(+e.target.value); });
  $("#btn-randomize").addEventListener("click", () => { $("#seed").value = Math.floor(Math.random() * 2 ** 31); });
  $("#multi-level").addEventListener("change", (e) => $("#levels-row").classList.toggle("is-on", e.target.checked));
  $("#btn-generate").addEventListener("click", () => doGenerate(false));
  $("#btn-reroll").addEventListener("click", () => doGenerate(true));
  $$("#grid-toggle button").forEach((b) => b.addEventListener("click", () => setGridMode(b.dataset.grid)));
  $("#btn-edit-mode").addEventListener("click", () => toggleEditMode());
  $("#edit-overlay").addEventListener("click", onEditClick);

  const cb = $("#cb-mode");
  try { const saved = localStorage.getItem("ttrpg_cb") === "1"; cb.checked = saved; setColorblind(saved); } catch {}
  cb.addEventListener("change", (e) => setColorblind(e.target.checked));

  let searchTimer = null;
  $("#lib-search").addEventListener("input", (e) => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { state.libFilters.search = e.target.value.trim(); loadLibrary(); }, 260); });
  $("#lib-biome").addEventListener("change", (e) => { state.libFilters.biome = e.target.value; loadLibrary(); });
  $("#lib-sort").addEventListener("change", (e) => { state.libFilters.sort = e.target.value; loadLibrary(); });
  $("#lib-fav-filter").addEventListener("click", (e) => { state.libFilters.favorite = !state.libFilters.favorite; e.currentTarget.classList.toggle("is-on", state.libFilters.favorite); loadLibrary(); });
  $("#lib-select-toggle").addEventListener("click", () => setSelecting(!state.selecting));

  $("#bulk-cancel").addEventListener("click", () => setSelecting(false));
  $("#bulk-zip").addEventListener("click", () => { if (!state.selected.size) return toast("Select at least one map", "info"); download(Api.bulkUrl([...state.selected]), "maps.zip"); });
  $("#bulk-foundry").addEventListener("click", () => { if (!state.selected.size) return toast("Select at least one map", "info"); download(Api.foundryModuleUrl([...state.selected], "ttrpg-maps"), "ttrpg-maps-module.zip"); });

  $("#btn-lib-export").addEventListener("click", () => download(Api.libraryExportUrl(), "ttrpg-library-backup.zip"));
  $("#btn-lib-import").addEventListener("click", () => $("#import-file").click());
  $("#import-file").addEventListener("change", async (e) => {
    const file = e.target.files[0]; e.target.value = "";
    if (!file) return;
    toast("Importing library…", "info");
    try { const r = await Api.libraryImport(file); toast(`Imported ${r.imported ?? "?"} maps`, "ok"); loadLibrary(); } catch (err) { toast("Import failed: " + err.message, "err", 5000); }
  });

  $("#confirm-ok").addEventListener("click", () => confirmAction && confirmAction());
  $("#confirm-cancel").addEventListener("click", () => closeOverlay("#confirm-overlay"));

  $$(".overlay").forEach((o) => o.addEventListener("click", (e) => { if (e.target === o) closeOverlay("#" + o.id); }));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") { closeFlyout(); $$(".overlay.is-open").forEach((o) => closeOverlay("#" + o.id)); } });
  document.addEventListener("click", (e) => {
    if (state.editMode && !e.target.closest(".edit-flyout") && !e.target.closest("#edit-overlay") && !e.target.closest("#preview-img")) closeFlyout();
  }, true);
}

/* ============================ boot ============================ */
async function boot() {
  wire();
  $("#density-val").textContent = densityLabel(+$("#density").value);
  try {
    const p = await Api.palette();
    state.palette = p;
    (p.terrains || []).forEach((t) => state.paletteByTerrain[t.id || t] = t);
    (p.props || []).forEach((pr) => state.paletteByProp[pr.type || pr.id] = pr);
  } catch { state.palette = { biomes: DEFAULT_BIOMES, terrains: [], props: [] }; }
  renderBiomePicker();
  runEstimate();
  refreshStats();
  const wanted = new URLSearchParams(location.search).get("view");
  if (wanted === "library") setView("library");
}

boot();
