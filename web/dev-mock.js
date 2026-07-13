/* ============================================================================
   dev-mock.js — DEVELOPMENT-ONLY fetch monkeypatch.

   NOT referenced by index.html. app.js imports this ONLY when the page URL
   carries ?mock=1. It replaces window.fetch with an in-memory implementation
   of Contract 4 so the SPA can be exercised end-to-end without the Flask
   backend running. Everything here is synthetic; the production path never
   touches this file.
   ============================================================================ */

// A 6×4-checker parchment-ish PNG as a data URI (tiny, decorative preview art).
function tileDataUri(hue, label) {
  const c = document.createElement("canvas");
  c.width = 300; c.height = 200;
  const g = c.getContext("2d");
  // base
  const grad = g.createLinearGradient(0, 0, 300, 200);
  grad.addColorStop(0, `hsl(${hue},32%,32%)`);
  grad.addColorStop(1, `hsl(${(hue + 30) % 360},28%,20%)`);
  g.fillStyle = grad; g.fillRect(0, 0, 300, 200);
  // faux terrain blotches
  for (let i = 0; i < 40; i++) {
    g.fillStyle = `hsla(${(hue + (i * 17) % 60)},40%,${25 + (i % 5) * 6}%,.5)`;
    const x = (i * 53) % 300, y = (i * 71) % 200, r = 8 + (i % 4) * 7;
    g.beginPath(); g.arc(x, y, r, 0, 7); g.fill();
  }
  // props hint
  g.fillStyle = "rgba(20,15,10,.55)";
  for (let i = 0; i < 6; i++) { const x = 30 + i * 45, y = 40 + ((i * 60) % 120); g.fillRect(x, y, 22, 22); }
  // label
  g.fillStyle = "rgba(255,240,210,.85)"; g.font = "13px sans-serif"; g.fillText(label || "", 10, 190);
  return c.toDataURL("image/png");
}
function griddedDataUri(hue, label, cols, rows) {
  const c = document.createElement("canvas");
  c.width = 300; c.height = 200;
  const g = c.getContext("2d");
  const img = new Image();
  // draw base synchronously via a fresh render
  const base = tileDataUri(hue, label);
  // We can't await here; just re-render base pattern then overlay grid.
  const grad = g.createLinearGradient(0, 0, 300, 200);
  grad.addColorStop(0, `hsl(${hue},32%,32%)`); grad.addColorStop(1, `hsl(${(hue + 30) % 360},28%,20%)`);
  g.fillStyle = grad; g.fillRect(0, 0, 300, 200);
  for (let i = 0; i < 40; i++) { g.fillStyle = `hsla(${(hue + (i * 17) % 60)},40%,${25 + (i % 5) * 6}%,.5)`; const x = (i * 53) % 300, y = (i * 71) % 200, r = 8 + (i % 4) * 7; g.beginPath(); g.arc(x, y, r, 0, 7); g.fill(); }
  g.fillStyle = "rgba(20,15,10,.55)"; for (let i = 0; i < 6; i++) { const x = 30 + i * 45, y = 40 + ((i * 60) % 120); g.fillRect(x, y, 22, 22); }
  // grid overlay
  g.strokeStyle = "rgba(0,0,0,.35)"; g.lineWidth = 1;
  const stepX = 300 / cols, stepY = 200 / rows;
  for (let x = 0; x <= cols; x++) { g.beginPath(); g.moveTo(x * stepX, 0); g.lineTo(x * stepX, 200); g.stroke(); }
  for (let y = 0; y <= rows; y++) { g.beginPath(); g.moveTo(0, y * stepY); g.lineTo(300, y * stepY); g.stroke(); }
  return c.toDataURL("image/png");
}

const TERRAINS = [
  "grass", "forest_floor", "stone_floor", "wood_floor", "cave_floor", "water_shallow",
  "water_deep", "sand", "snow", "ice", "lava", "rubble", "mud", "cobblestone", "stone_wall", "cave_wall",
];
const PROPS = [
  { type: "crate", display: "Crate", category: "cover", footprint: [1, 1], rotatable: true },
  { type: "barrel", display: "Barrel", category: "cover", footprint: [1, 1], rotatable: true },
  { type: "rock", display: "Boulder", category: "cover", footprint: [1, 1], rotatable: true },
  { type: "table", display: "Table", category: "furniture", footprint: [2, 1], rotatable: true },
  { type: "chair", display: "Chair", category: "furniture", footprint: [1, 1], rotatable: true },
  { type: "tree_pine", display: "Pine", category: "nature", footprint: [2, 2], rotatable: false },
  { type: "bush", display: "Bush", category: "nature", footprint: [1, 1], rotatable: false },
  { type: "altar", display: "Altar", category: "focal", footprint: [2, 2], rotatable: true },
  { type: "campfire", display: "Campfire", category: "focal", footprint: [1, 1], rotatable: false },
  { type: "well", display: "Well", category: "focal", footprint: [2, 2], rotatable: false },
  { type: "door", display: "Door", category: "structure", footprint: [1, 1], rotatable: true },
  { type: "stairs_down", display: "Stairs Down", category: "connection", footprint: [1, 2], rotatable: true },
];

let counter = 0;
const DB = new Map(); // id -> {record, layout, hue}

function makeLayout(cols, rows, seed) {
  const cells = [];
  for (let y = 0; y < rows; y++) { const row = []; for (let x = 0; x < cols; x++) row.push(TERRAINS[(x + y + seed) % 6]); cells.push(row); }
  const props = [];
  for (let i = 0; i < Math.min(14, Math.floor(cols * rows / 40)); i++) {
    const def = PROPS[(i + seed) % PROPS.length];
    props.push({ id: "p" + String(i).padStart(3, "0"), type: def.type, x: (i * 3 + 1) % (cols - 2), y: (i * 5 + 1) % (rows - 2), rot: (i % 4) * 90, variant: 0 });
  }
  return { cols, rows, seed, spec: {}, cells, props,
    focal: { x: Math.floor(cols / 2), y: Math.floor(rows / 2), prop_id: "p001", kind: "altar" },
    spawns: [{ x: 2, y: rows - 3, role: "player" }, { x: cols - 3, y: 3, role: "enemy" }],
    connections: [], indoor: seed % 2 === 0, walls: [] };
}

function makeRecord(spec, setId, floorIndex) {
  counter++;
  const id = (Date.now().toString(16) + counter).slice(-12);
  const hue = (counter * 47) % 360;
  const cols = spec.cols || 30, rows = spec.rows || 20, px = spec.px_per_square || 140;
  const title = spec.title || spec.prompt || `Mock Map ${counter}`;
  const biomePool = ["dungeon", "cave", "forest", "coast", "arctic", "ruins", "mine", "village"];
  const biomes = spec.biomes && spec.biomes.length ? spec.biomes : [biomePool[counter % biomePool.length]];
  const record = {
    id, title, prompt: spec.prompt || title, created_at: new Date(Date.now() - counter * 3600e3).toISOString(),
    cols, rows, px_per_square: px, feet_per_square: spec.feet_per_square || 5,
    seed: spec.seed || Math.floor(Math.random() * 2 ** 31), density: spec.density ?? 0.5, mood: spec.mood || "neutral",
    biomes, tags: [], favorite: false, set_id: setId || null, floor_index: floorIndex ?? 0,
    width_px: cols * px, height_px: rows * px, bytes: cols * rows * px * 40,
    thumb_url: tileDataUri(hue, title.slice(0, 18)),
    warnings: (cols * px) * (rows * px) > 50e6 ? ["Large map may exceed some VTT ceilings."] : [],
  };
  DB.set(id, { record, layout: makeLayout(cols, rows, record.seed), hue,
    imgGridless: tileDataUri(hue, title.slice(0, 18)), imgGridded: griddedDataUri(hue, title.slice(0, 18), cols, rows) });
  return record;
}

// seed a few maps so Library isn't empty
function seedLibrary() {
  makeRecord({ prompt: "Collapsed dwarven mine with underground river", cols: 30, rows: 20, mood: "eerie", biomes: ["mine", "cave"] });
  makeRecord({ prompt: "Pirate cove ambush", cols: 40, rows: 30, mood: "combat", biomes: ["coast"] });
  makeRecord({ prompt: "Overgrown elven ruins", cols: 30, rows: 20, mood: "peaceful", biomes: ["forest", "ruins"] });
  const setId = "set" + Date.now().toString(16).slice(-6);
  makeRecord({ prompt: "Frozen watchtower — ground floor", cols: 20, rows: 20, mood: "tense", biomes: ["arctic"] }, setId, 0);
  makeRecord({ prompt: "Frozen watchtower — upper floor", cols: 20, rows: 20, mood: "tense", biomes: ["arctic"] }, setId, 1);
  // give the first one a favorite + tags for filter testing
  const first = [...DB.values()][0].record; first.favorite = true; first.tags = ["Campaign: Ashen Crown", "Session 4"];
}
seedLibrary();

function json(body, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}
function dataUriToResponse(uri) {
  const [meta, b64] = uri.split(",");
  const mime = meta.split(":")[1].split(";")[0];
  const bin = atob(b64); const arr = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
  return new Response(arr, { status: 200, headers: { "Content-Type": mime } });
}

const realFetch = window.fetch.bind(window);

async function mockFetch(input, init = {}) {
  const url = typeof input === "string" ? input : input.url;
  const u = new URL(url, location.origin);
  const path = u.pathname;
  const method = (init.method || "GET").toUpperCase();
  const q = u.searchParams;
  const body = init.body ? safeJson(init.body) : {};
  await delay(120 + Math.random() * 200); // simulate latency

  // --- estimate ---
  if (path === "/api/estimate" && method === "POST") {
    const { cols, rows, px_per_square: px } = body;
    const w = cols * px, h = rows * px, mp = (w * h) / 1e6, bytes = Math.round(w * h * 0.55);
    const warnings = [];
    if (mp > 50) warnings.push(`Very large: ${mp.toFixed(0)} MP — over the 50 MP comfort ceiling, may exceed Owlbear's limit.`);
    else if (bytes > 20 * 1024 * 1024) warnings.push(`Large file (~${(bytes / 1048576).toFixed(0)} MB) — over 20 MB comfort ceiling.`);
    return json({ width: w, height: h, megapixels: +mp.toFixed(2), est_bytes: bytes, warnings });
  }

  // --- generate ---
  if (path === "/api/generate" && method === "POST") {
    if (body.multi_level && (body.levels || 2) > 1) {
      const setId = "set" + Date.now().toString(16).slice(-6);
      const maps = [];
      for (let i = 0; i < (body.levels || 2); i++) maps.push(makeRecord({ ...body, seed: (body.seed || 1) + i }, setId, i));
      return json({ maps });
    }
    return json({ map: makeRecord(body) });
  }

  // --- list maps ---
  if (path === "/api/maps" && method === "GET") {
    let maps = [...DB.values()].map((e) => e.record);
    const setFilter = q.get("set");
    if (setFilter) maps = maps.filter((m) => m.set_id === setFilter);
    const search = (q.get("search") || "").toLowerCase();
    if (search) maps = maps.filter((m) => (m.title + " " + m.prompt).toLowerCase().includes(search));
    const biome = q.get("biome");
    if (biome) maps = maps.filter((m) => (m.biomes || []).includes(biome));
    if (q.get("favorite") === "1") maps = maps.filter((m) => m.favorite);
    maps.sort((a, b) => q.get("sort") === "oldest" ? new Date(a.created_at) - new Date(b.created_at) : new Date(b.created_at) - new Date(a.created_at));
    const total_bytes = [...DB.values()].reduce((s, e) => s + e.record.bytes, 0);
    return json({ maps, total_count: DB.size, total_bytes });
  }

  // --- map detail / patch / delete / files ---
  const mDetail = path.match(/^\/api\/maps\/([^/]+)$/);
  if (mDetail) {
    const id = mDetail[1]; const entry = DB.get(id);
    if (!entry) return json({ error: "Not found" }, 404);
    if (method === "GET") return json({ map: entry.record, layout: entry.layout });
    if (method === "PATCH") { Object.assign(entry.record, body); return json({ map: entry.record }); }
    if (method === "DELETE") { DB.delete(id); return new Response(null, { status: 204 }); }
  }

  const mFile = path.match(/^\/api\/maps\/([^/]+)\/file\/([^/]+)$/);
  if (mFile) {
    const entry = DB.get(mFile[1]); if (!entry) return json({ error: "Not found" }, 404);
    const kind = mFile[2];
    if (kind === "gridded") return dataUriToResponse(entry.imgGridded);
    if (kind === "metadata") { const m = entry.record; return json({ title: m.title, prompt: m.prompt, grid: { columns: m.cols, rows: m.rows, px_per_square: m.px_per_square, feet_per_square: m.feet_per_square }, image: { width: m.width_px, height: m.height_px }, foundry: { grid_size: m.px_per_square, scene_width: m.width_px, scene_height: m.height_px, grid_type: "Square", global_illumination: !entry.layout.indoor }, owlbear: { grid_size_px: m.px_per_square, grid_type: "Square", cell_size_ft: m.feet_per_square }, seed: m.seed, biomes: m.biomes }); }
    return dataUriToResponse(entry.imgGridless);
  }

  const mEdit = path.match(/^\/api\/maps\/([^/]+)\/edit$/);
  if (mEdit && method === "POST") {
    const entry = DB.get(mEdit[1]); if (!entry) return json({ error: "Not found" }, 404);
    const L = entry.layout;
    (body.set_cells || []).forEach((c) => { if (L.cells[c.y]) L.cells[c.y][c.x] = c.terrain; });
    (body.props_remove || []).forEach((pid) => { L.props = L.props.filter((p) => p.id !== pid); });
    (body.props_update || []).forEach((u) => { const p = L.props.find((x) => x.id === u.id); if (p) Object.assign(p, u); });
    entry.record._edited = true;
    return json({ map: entry.record, layout: L });
  }
  const mRevert = path.match(/^\/api\/maps\/([^/]+)\/revert$/);
  if (mRevert && method === "POST") {
    const entry = DB.get(mRevert[1]); if (!entry) return json({ error: "Not found" }, 404);
    entry.layout = makeLayout(entry.record.cols, entry.record.rows, entry.record.seed);
    return json({ map: entry.record, layout: entry.layout });
  }

  // --- stats ---
  if (path === "/api/stats") {
    const by_biome = {};
    for (const e of DB.values()) for (const b of (e.record.biomes || [])) by_biome[b] = (by_biome[b] || 0) + 1;
    const total_bytes = [...DB.values()].reduce((s, e) => s + e.record.bytes, 0);
    return json({ map_count: DB.size, total_bytes, by_biome });
  }

  // --- palette ---
  if (path === "/api/assets/palette") {
    return json({
      biomes: ["forest", "jungle", "grassland", "desert", "swamp", "arctic", "mountain", "cave", "coast", "sea", "river", "volcanic", "dungeon", "ruins", "village", "tavern", "temple", "crypt", "sewer", "mine", "ship", "camp", "fey"].map((id) => ({ id, display: id.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()) })),
      terrains: TERRAINS.map((id) => ({ id, display: id.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()), preview: tileDataUri((TERRAINS.indexOf(id) * 24) % 360, "") })),
      props: PROPS.map((p) => ({ ...p, preview: tileDataUri((PROPS.indexOf(p) * 30) % 360, p.display.slice(0, 6)) })),
    });
  }

  // --- exports / library backup: return a tiny blob ---
  if (path.startsWith("/api/export/") || path === "/api/library/export" || path.endsWith("/print-pdf")) {
    return new Response(new Blob(["mock-archive"], { type: "application/octet-stream" }), { status: 200 });
  }
  if (path === "/api/library/import" && method === "POST") {
    return json({ imported: 2 });
  }

  // fall through to the real fetch (e.g. loading style.css, dev-mock.js itself)
  return realFetch(input, init);
}

function safeJson(s) { try { return JSON.parse(s); } catch { return {}; } }
function delay(ms) { return new Promise((r) => setTimeout(r, ms)); }

/* Resolve a mock file URL to an in-memory data URI. <img src="…"> loads bypass
   window.fetch entirely, so in mock mode we intercept the <img>.src SETTER and
   swap Contract-4 file URLs for the synthetic data URIs. Production is unaffected
   (this whole file only loads under ?mock=1). */
function resolveMockFileUrl(url) {
  if (typeof url !== "string") return null;
  const m = url.match(/\/api\/maps\/([^/]+)\/file\/([^/?]+)/);
  if (!m) return null;
  const entry = DB.get(m[1]);
  if (!entry) return null;
  if (m[2] === "gridded") return entry.imgGridded;
  if (m[2] === "thumb") return entry.record.thumb_url;
  return entry.imgGridless; // gridless (default)
}
function patchImageSrc() {
  // (1) property setter: img.src = "…"
  const proto = HTMLImageElement.prototype;
  const desc = Object.getOwnPropertyDescriptor(proto, "src");
  if (desc && desc.set) {
    Object.defineProperty(proto, "src", {
      configurable: true, enumerable: desc.enumerable,
      get() { return desc.get.call(this); },
      set(v) { desc.set.call(this, resolveMockFileUrl(v) || v); },
    });
  }
  // (2) attribute path: el.setAttribute("src", "…")
  const rawSetAttr = Element.prototype.setAttribute;
  Element.prototype.setAttribute = function (name, value) {
    if (name === "src") { const r = resolveMockFileUrl(value); if (r) value = r; }
    return rawSetAttr.call(this, name, value);
  };
}

export function installMock() {
  window.fetch = mockFetch;
  patchImageSrc();
  console.info("%c[dev-mock] fetch + <img>.src monkeypatched — Contract 4 served from memory.", "color:#d9a441");
}
