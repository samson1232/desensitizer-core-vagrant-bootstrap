const $ = (id) => document.getElementById(id);

const EXAMPLE = `Support ticket #4471 — escalation needed

Customer: Alice Smith
Email: alice.smith@example.com
Phone: +447700900123
Staff ref: STF-4821
NI: QQ 12 34 56 C
Postcode: SW1A 1AA
DOB: 1984-07-19
Workstation: ops-ws-014 (192.0.2.44, MAC 00:1B:44:11:3A:B7)
IBAN: GB33BUKB20201555555555
Session id: 123e4567-e89b-12d3-a456-426614174000

She cannot log in after the VPN change. Please confirm whether her workstation
certificate is bound to the old MAC, and rewrite the following note for the
customer so it sounds calm and does not repeat the identifiers twice.`;

const TOKEN = /([a-z][a-z0-9_]{1,31}):([A-Z2-7]{26})/g;

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function paint(el, text) {
  if (text === null || text === undefined || text === "") { el.textContent = "—"; return; }
  el.innerHTML = escapeHtml(text).replace(TOKEN, (m, kind) => `<mark class="tok" data-kind="${kind}">${m}</mark>`);
}

function setStatus(msg, cls = "") {
  const el = $("status");
  el.textContent = msg;
  el.className = "statusline " + cls;
}

function chips(kinds) {
  const el = $("chips");
  el.innerHTML = "";
  Object.entries(kinds || {}).forEach(([kind, n]) => {
    const s = document.createElement("span");
    s.className = "chip";
    s.textContent = `${kind} ×${n}`;
    el.appendChild(s);
  });
  if (!Object.keys(kinds || {}).length) el.innerHTML = '<span class="chip muted">no values detected</span>';
}

async function api(path, body) {
  const res = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({ error: "server returned non-JSON" }));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

async function health() {
  try {
    const h = await api("/api/health");
    $("badge-model").textContent = `model: ${h.model}`;
    const live = $("badge-live");
    live.textContent = h.live ? "live: on" : "live: off (demo only)";
    live.className = "badge " + (h.live ? "good" : "warn");
    $("btn-live").disabled = !h.live;
    $("btn-live").title = h.live ? "send to the configured cloud model" : "start ui_server.py with --live to enable";
    setStatus(`ready · ${h.kinds.length} detector kinds`, "ok");
  } catch (err) {
    setStatus(`cannot reach server: ${err.message}`, "bad");
  }
}

async function censorOnly() {
  const text = $("input").value;
  if (!text.trim()) return setStatus("nothing to censor", "bad");
  setStatus("censoring…");
  try {
    const r = await api("/api/censor", { text });
    paint($("censored"), r.censored);
    chips(r.kinds);
    setStatus(`censored ${Object.values(r.kinds).reduce((a, b) => a + b, 0)} value(s) in ${Object.keys(r.kinds).length} kind(s)` +
      (r.leaks.length ? ` — LEAK: ${r.leaks.join(", ")}` : " — post-scan clean"), r.leaks.length ? "bad" : "ok");
  } catch (err) { setStatus(`censor failed: ${err.message}`, "bad"); }
}

async function run(mode) {
  const text = $("input").value;
  if (!text.trim()) return setStatus("nothing to send", "bad");
  setStatus(mode === "live" ? "sending to the cloud…" : "running offline demo…");
  $("runmeta").textContent = "";
  try {
    const r = await api("/api/run", { text, mode });
    if (r.refused) {
      paint($("censored"), null); paint($("raw"), null); paint($("restored"), null);
      setStatus(`refused at ${r.refused.stage}: ${r.refused.reason}`, "bad");
      return;
    }
    paint($("censored"), r.censored);
    paint($("raw"), r.raw);
    paint($("restored"), r.restored);
    chips(r.kinds);
    const exact = r.restored.includes(text.trim());
    $("runmeta").textContent = `${r.run_id} · ${r.elapsed_ms} ms · ${r.mode}`;
    const tokCount = (r.raw.match(TOKEN) || []).length;
    setStatus(`round trip ok · ${tokCount} token(s) restored${exact ? " · byte-exact" : ""}`, exact ? "ok" : "warn");
  } catch (err) { setStatus(`run failed: ${err.message}`, "bad"); }
}

async function restorePanel() {
  const text = $("censored").textContent;
  if (!text || text === "—") return setStatus("nothing in the middle panel", "bad");
  try {
    const r = await api("/api/decensor", { text });
    if (r.error) return setStatus(`refused: ${r.error}`, "bad");
    paint($("restored"), r.restored);
    setStatus("restored from the vault (no model involved)", "ok");
  } catch (err) { setStatus(`restore failed: ${err.message}`, "bad"); }
}

$("btn-example").onclick = () => { $("input").value = EXAMPLE; setStatus("example loaded"); };
$("btn-clear").onclick = () => {
  $("input").value = "";
  ["censored", "raw", "restored"].forEach((id) => paint($(id), null));
  $("chips").innerHTML = ""; $("runmeta").textContent = ""; setStatus("cleared");
};
$("btn-censor").onclick = censorOnly;
$("btn-demo").onclick = () => run("demo");
$("btn-live").onclick = () => run("live");
$("btn-restore").onclick = restorePanel;

health();

// ---------------------------------------------------------------- corpus panel
let WORKSPACE = null;

function renderDocs(ws) {
  const list = $("docs");
  list.innerHTML = "";
  if (!ws || !ws.enabled) {
    list.innerHTML = '<li class="empty">no workspace configured — start ui_server.py with --workspace-root and --project</li>';
    return;
  }
  if (!ws.documents.length) {
    list.innerHTML = '<li class="empty">no documents yet — upload a pdf, docx or xlsx</li>';
    return;
  }
  ws.documents.forEach((d) => {
    const li = document.createElement("li");
    const kinds = Object.entries(d.kinds || {}).map(([k, n]) => `${k}×${n}`).join(", ") || "no placeholders";
    li.innerHTML = `<code>${escapeHtml(d.id)}</code> <span class="name">${escapeHtml(d.source_name)}</span>` +
                   `<span class="kinds">${escapeHtml(kinds)}</span>`;
    li.title = d.aliased_path;
    list.appendChild(li);
  });
}

async function loadWorkspace() {
  try {
    const ws = await api("/api/workspace");
    WORKSPACE = ws;
    const badge = $("badge-ws");
    badge.textContent = ws.enabled ? `corpus: ${ws.project}` : "corpus: off";
    badge.className = "badge " + (ws.enabled ? "good" : "warn");
    if (ws.enabled) {
      $("ws-path").textContent = ws.work_dir;
      $("btn-upload").disabled = false;
      $("btn-index").disabled = false;
      $("btn-copy").disabled = false;
    } else {
      $("ws-path").textContent = "";
      ["btn-upload", "btn-index", "btn-copy"].forEach((id) => { $(id).disabled = true; });
    }
    renderDocs(ws);
  } catch (err) {
    setStatus(`workspace unavailable: ${err.message}`, "bad");
  }
}

function readAsBase64(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onerror = () => reject(new Error(`cannot read ${file.name}`));
    r.onload = () => resolve(String(r.result).split(",")[1] || "");
    r.readAsDataURL(file);
  });
}

async function uploadFiles(files) {
  if (!files || !files.length) return;
  setStatus(`aliasing ${files.length} document(s)…`);
  let added = 0, failed = 0;
  for (const file of files) {
    try {
      const data = await readAsBase64(file);
      const res = await api("/api/upload", { name: file.name, data });
      added += 1;
      const d = res.document;
      const kinds = Object.entries(d.kinds || {}).map(([k, n]) => `${k}×${n}`).join(", ") || "no placeholders";
      setStatus(`${d.id} ← ${file.name}: ${kinds}`, "ok");
      renderDocs(res.workspace);
    } catch (err) {
      failed += 1;
      setStatus(`${file.name}: ${err.message}`, "bad");
    }
  }
  if (added) setStatus(`aliased ${added} document(s)${failed ? `, ${failed} failed` : ""} — the agent can now work in work/`, "ok");
}

$("btn-upload").onclick = () => $("file").click();
$("file").onchange = async (event) => {
  await uploadFiles(Array.from(event.target.files || []));
  event.target.value = "";
};
$("btn-index").onclick = async () => {
  try {
    const r = await api("/api/index");
    const view = $("index-view");
    view.hidden = !view.hidden;
    if (!view.hidden) view.textContent = r.index;
  } catch (err) { setStatus(`index: ${err.message}`, "bad"); }
};
$("btn-copy").onclick = async () => {
  if (!WORKSPACE || !WORKSPACE.work_dir) return;
  try {
    await navigator.clipboard.writeText(WORKSPACE.work_dir);
    setStatus(`copied ${WORKSPACE.work_dir}`, "ok");
  } catch { setStatus(WORKSPACE.work_dir); }
};

loadWorkspace();
