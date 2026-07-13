/* ============================================================================
   api.js — thin fetch wrapper over Contract 4 (HTTP API, all under /api).
   Same-origin, plain fetch. No external calls. Every function returns parsed
   JSON (or a Blob / URL for file endpoints) and throws an Error with the
   server's {error} message on non-2xx.
   ============================================================================ */

const BASE = "/api";

async function jsonFetch(path, opts = {}) {
  let res;
  try {
    res = await fetch(BASE + path, opts);
  } catch (e) {
    throw new Error("Network error — is the server running? (" + e.message + ")");
  }
  if (res.status === 204) return null;
  const ct = res.headers.get("content-type") || "";
  let body = null;
  if (ct.includes("application/json")) {
    body = await res.json().catch(() => null);
  }
  if (!res.ok) {
    const msg = (body && body.error) || res.statusText || ("HTTP " + res.status);
    throw new Error(msg);
  }
  return body;
}

function qs(params) {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params || {})) {
    if (v === undefined || v === null || v === "") continue;
    p.set(k, v);
  }
  const s = p.toString();
  return s ? "?" + s : "";
}

export const Api = {
  // --- generation ---
  generate(spec) {
    return jsonFetch("/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(spec),
    });
  },
  estimate(cols, rows, px_per_square) {
    return jsonFetch("/estimate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cols, rows, px_per_square }),
    });
  },

  // --- library listing / detail ---
  listMaps(filters) {
    return jsonFetch("/maps" + qs(filters));
  },
  getMap(id) {
    return jsonFetch("/maps/" + id);
  },
  patchMap(id, fields) {
    return jsonFetch("/maps/" + id, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(fields),
    });
  },
  deleteMap(id) {
    return jsonFetch("/maps/" + id, { method: "DELETE" });
  },

  // --- editing ---
  editMap(id, patch) {
    return jsonFetch("/maps/" + id + "/edit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    });
  },
  revertMap(id) {
    return jsonFetch("/maps/" + id + "/revert", { method: "POST" });
  },

  // --- stats / assets ---
  stats() {
    return jsonFetch("/stats");
  },
  palette() {
    return jsonFetch("/assets/palette");
  },
  libraryImport(file) {
    const fd = new FormData();
    fd.append("archive", file, file.name);
    return jsonFetch("/library/import", { method: "POST", body: fd });
  },

  // --- URL builders for direct downloads (browser handles the file save) ---
  fileUrl(id, kind) {
    return BASE + "/maps/" + id + "/file/" + kind;
  },
  thumbUrl(record) {
    // Prefer the record-provided thumb_url; fall back to the file endpoint.
    return record.thumb_url || BASE + "/maps/" + record.id + "/file/thumb";
  },
  printPdfUrl(id, paper) {
    return BASE + "/maps/" + id + "/print-pdf" + qs({ paper });
  },
  bulkUrl(ids) {
    return BASE + "/export/bulk" + qs({ ids: ids.join(",") });
  },
  foundryModuleUrl(ids, name) {
    return BASE + "/export/foundry-module" + qs({ ids: ids.join(","), name });
  },
  libraryExportUrl() {
    return BASE + "/library/export";
  },
};
