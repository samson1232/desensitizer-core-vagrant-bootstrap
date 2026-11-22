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
