"use strict";
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const S = {me: null, projects: null, project: null, issues: null, status: "All", q: "", loading: false};

/* ---------- plumbing ---------- */
async function api(tool, body = {}) {
  const t0 = performance.now();
  let r, j;
  try {
    r = await fetch("/api/tool/" + tool, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
    j = await r.json();
  } catch { j = {error: "Cannot reach the web app."}; r = {ok: false, status: 0}; }
  const ms = Math.round(performance.now() - t0);
  if (!r.ok) {
    activity(tool, false, ms, j.error);
    if (r.status === 401) { S.me = null; route(); }
    if (j.code === "jira_not_connected" && S.me) { S.me.jira.connected = false; route(); }
    throw Object.assign(new Error(j.error || "Request failed"), {status: r.status});
  }
  activity(tool, true, ms);
  return j.result;
}

function toast(msg, kind = "ok", html = false) {
  const el = document.createElement("div");
  el.className = "toast " + (kind === "error" ? "error" : "");
  el.setAttribute("role", kind === "error" ? "alert" : "status");
  el[html ? "innerHTML" : "textContent"] = msg;
  $("toasts").append(el);
  setTimeout(() => el.remove(), kind === "error" ? 7000 : 4500);
}

const acts = [];
function activity(tool, ok, ms, err) {
  acts.unshift({t: new Date().toLocaleTimeString(), tool, ok, ms, err});
  acts.length = Math.min(acts.length, 60);
  $("actCount").textContent = acts.length;
  $("actList").innerHTML = acts.map(a =>
    `<li class="${a.ok ? "ok" : "bad"}">${esc(a.t)} ${a.ok ? "✓" : "✗"} ${esc(a.tool)} ${a.ms}ms${a.err ? " - " + esc(a.err) : ""}</li>`).join("");
}

const initial = s => esc((s || "?").trim()[0]?.toUpperCase() || "?");
const avatar = (name, pic) =>
  `<span class="avatar">${initial(name)}${pic ? `<img src="${esc(pic)}" alt="" referrerpolicy="no-referrer" onerror="this.remove()">` : ""}</span>`;
const pillClass = s => /done|closed|resolved|complete/i.test(s) ? "done" : /progress|review|doing|testing/i.test(s) ? "prog" : "todo";
const isAdmin = () => S.me?.role === "admin";

/* ---------- routing between views ---------- */
function show(view) {
  for (const v of ["loading", "signin", "connect", "app"]) $("v-" + v).hidden = v !== view;
  $("top").hidden = !S.me;
  $("activity").hidden = view !== "app";
  const step = view === "signin" ? 1 : view === "connect" ? 2 : 3;
  const needsJira = S.me ? S.me.jira.mode === "user" : true;
  $("stepper").hidden = view === "app" || view === "loading";
  const steps = [["Sign in with Google"], ...(needsJira ? [["Connect Jira"]] : []), ["Start working"]];
  const cur = needsJira ? step : (step === 3 ? 2 : 1);
  $("stepper").innerHTML = steps.map((s, i) => {
    const n = i + 1, cls = n < cur ? "done" : n === cur ? "on" : "";
    return `<li class="${cls}"><span class="n">${n < cur ? "✓" : n}</span>${esc(s[0])}</li>`;
  }).join("");
}

function route() {
  renderHeader();
  if (!S.me) return show("signin");
  if (S.me.jira.mode === "user" && !S.me.jira.connected) return show("connect");
  show("app");
  $("btnNew").disabled = !isAdmin();
  $("btnNew").title = isAdmin() ? "" : "Only admins can create tasks";
  if (!S.projects) loadProjects();
}

function renderHeader() {
  const chip = $("jiraChip"), m = S.me;
  if (!m) { $("userBtn").innerHTML = ""; chip.hidden = true; return; }
  chip.hidden = false;
  const j = m.jira;
  chip.className = "chip" + (j.connected ? "" : " off");
  chip.textContent = j.mode === "service" ? "Shared service account"
    : j.connected ? (j.account ? `${j.account} · ` : "") + (j.site || "").replace(/^https?:\/\//, "") : "Jira not connected";
  $("userBtn").innerHTML = `${avatar(m.name, m.picture)}<span>${esc(m.name.split(" ")[0])}</span><span class="badge ${esc(m.role)}">${esc(m.role)}</span>`;
  const src = {google_group: "Role from Google group", admin_emails: "Role from admin list", default: "Default role"}[m.role_source] || "";
  $("userMenu").innerHTML = `<div class="who"><b>${esc(m.name)}</b><span>${esc(m.email)}</span><br><span>${esc(src)}</span></div>
    ${m.jira.mode === "user" && m.jira.connected ? '<button data-act="disc" role="menuitem">Disconnect Jira</button>' : ""}
    <button data-act="out" role="menuitem">Sign out</button>`;
}

/* ---------- data ---------- */
async function loadProjects() {
  $("projects").innerHTML = Array(4).fill('<li><div class="skel" style="margin:12px 8px"></div></li>').join("");
  try { S.projects = await api("list_projects"); } catch (e) { S.projects = null; $("projects").innerHTML = `<li class="state sm">${esc(e.message)}</li>`; return; }
  renderProjects();
  if (S.projects.length && !S.project) selectProject(S.projects[0].key);
}

function renderProjects() {
  const q = $("projSearch").value.toLowerCase();
  const list = (S.projects || []).filter(p => (p.key + p.name).toLowerCase().includes(q));
  $("projects").innerHTML = list.map(p => `<li><button data-key="${esc(p.key)}" aria-current="${p.key === S.project}">
    <span class="key">${esc(p.key)}</span><span>${esc(p.name)}</span></button></li>`).join("")
    || `<li class="state sm">${S.projects?.length ? "No match" : "No projects visible to your account"}</li>`;
  $("nProj").innerHTML = (S.projects || []).map(p => `<option value="${esc(p.key)}">${esc(p.key)} - ${esc(p.name)}</option>`).join("");
}

async function selectProject(key) {
  S.project = key; S.status = "All"; S.q = ""; $("issueSearch").value = "";
  const p = S.projects.find(x => x.key === key);
  $("projTitle").textContent = p ? p.name : key;
  $("projSub").textContent = key;
  renderProjects();
  await loadIssues();
}

async function loadIssues() {
  $("issueBox").innerHTML = `<div style="padding:16px;display:grid;gap:14px">${Array(5).fill('<div class="skel"></div>').join("")}</div>`;
  try { S.issues = await api("list_issues", {project: S.project, limit: 50}); }
  catch (e) { S.issues = null; $("issueBox").innerHTML = `<div class="state"><b>Could not load issues</b>${esc(e.message)}</div>`; return; }
  renderFilters(); renderIssues();
}

function renderFilters() {
  const names = ["All", ...new Set((S.issues || []).map(i => i.status).filter(Boolean))];
  $("statusFilter").innerHTML = names.map(n => `<button aria-pressed="${n === S.status}" data-status="${esc(n)}">${esc(n)}</button>`).join("");
}

function renderIssues() {
  const q = S.q.toLowerCase();
  const rows = (S.issues || []).filter(i => (S.status === "All" || i.status === S.status) && (!q || (i.key + i.summary).toLowerCase().includes(q)));
  if (!rows.length) {
    $("issueBox").innerHTML = `<div class="state"><b>${S.issues?.length ? "Nothing matches" : "No issues yet"}</b>${S.issues?.length ? "Try another filter." : isAdmin() ? "Create the first task with the button above." : "Ask an admin to add one."}</div>`;
    return;
  }
  $("issueBox").innerHTML = `<table><thead><tr><th>Key</th><th>Summary</th><th>Status</th><th>Assignee</th></tr></thead><tbody>${rows.map(i => `
    <tr data-key="${esc(i.key)}" tabindex="0"><td><span class="key">${esc(i.key)}</span></td><td class="sum" title="${esc(i.summary)}">${esc(i.summary)}</td>
    <td><span class="pill ${pillClass(i.status)}">${esc(i.status || "-")}</span></td>
    <td>${i.assignee ? `<span class="who-cell">${avatar(i.assignee)}${esc(i.assignee)}</span>` : '<span class="muted">Unassigned</span>'}</td></tr>`).join("")}</tbody></table>`;
}

/* ---------- drawer ---------- */
async function openIssue(key) {
  const d = $("drawer");
  d.innerHTML = `<div class="d-head"><div><span class="key">${esc(key)}</span></div><button class="icon-btn" data-close aria-label="Close">✕</button></div><div class="d-body"><div class="skel"></div><div class="skel"></div></div>`;
  d.hidden = $("scrim").hidden = false;
  d.querySelector("[data-close]").focus();
  try {
    const [i, ts] = await Promise.all([api("get_issue", {issue_key: key}), isAdmin() ? api("get_transitions", {issue_key: key}) : Promise.resolve([])]);
    d.innerHTML = `<div class="d-head"><div><span class="key">${esc(i.key)}</span> <span class="pill ${pillClass(i.status)}" id="dPill">${esc(i.status)}</span>
        <h2 id="dTitle">${esc(i.summary)}</h2></div><button class="icon-btn" data-close aria-label="Close">✕</button></div>
      <div class="d-body">
        <dl class="meta"><div><dt>Type</dt><dd>${esc(i.type || "-")}</dd></div><div><dt>Priority</dt><dd>${esc(i.priority || "-")}</dd></div>
        <div><dt>Assignee</dt><dd>${esc(i.assignee || "Unassigned")}</dd></div><div><dt>Reporter</dt><dd>${esc(i.reporter || "-")}</dd></div></dl>
        <div><dt class="muted sm">Description</dt><p class="desc">${esc(i.description) || '<span class="muted">No description.</span>'}</p></div>
        ${i.url ? `<a href="${esc(i.url)}" target="_blank" rel="noopener noreferrer">Open in Jira ↗</a>` : ""}
        ${isAdmin() ? `<form class="edit" id="editForm" data-key="${esc(i.key)}"><h3>Edit</h3>
          <label>Summary<input class="input" name="summary" value="${esc(i.summary)}" maxlength="250"></label>
          <label>Description<textarea class="input" name="description" rows="4">${esc(i.description)}</textarea></label>
          <label>Move to<select class="input" name="status"><option value="">Keep "${esc(i.status)}"</option>${ts.map(t => `<option value="${esc(t.to || t.name)}">${esc(t.to || t.name)}</option>`).join("")}</select></label>
          <div class="row end"><button class="btn primary" type="submit">Save changes</button></div></form>`
        : `<div class="lock">🔒 Read-only. Editing needs the admin role, which your organisation assigns.</div>`}
      </div>`;
    d.setAttribute("aria-labelledby", "dTitle");
  } catch (e) {
    d.querySelector(".d-body").innerHTML = `<div class="err">${esc(e.message)}</div>`;
  }
}
function closeDrawer() { $("drawer").hidden = $("scrim").hidden = true; }

/* ---------- events ---------- */
document.addEventListener("click", async ev => {
  const t = ev.target;
  if (!t.closest(".menu-wrap")) { $("userMenu").hidden = true; $("userBtn").setAttribute("aria-expanded", "false"); }
  if (t.closest("[data-close]") || t === $("scrim")) return closeDrawer();
  const proj = t.closest("#projects button[data-key]");
  if (proj) return selectProject(proj.dataset.key);
  const st = t.closest("[data-status]");
  if (st) { S.status = st.dataset.status; renderFilters(); return renderIssues(); }
  const row = t.closest("#issueBox tr[data-key]");
  if (row) return openIssue(row.dataset.key);
  const act = t.closest("[data-act]")?.dataset.act;
  if (act === "out") { await fetch("/api/logout", {method: "POST"}); S.me = null; S.projects = S.project = S.issues = null; closeDrawer(); route(); }
  if (act === "disc") {
    await api("disconnect_jira"); S.me.jira = {...S.me.jira, connected: false}; S.projects = S.project = S.issues = null;
    toast("Jira disconnected."); route();
  }
});
document.addEventListener("keydown", ev => {
  if (ev.key === "Escape") { closeDrawer(); if ($("dlgNew").open) $("dlgNew").close(); $("userMenu").hidden = true; }
  if (ev.key === "Enter" && ev.target.matches("#issueBox tr[data-key]")) openIssue(ev.target.dataset.key);
  if (ev.key === "/" && !/input|textarea|select/i.test(ev.target.tagName) && !$("v-app").hidden) { ev.preventDefault(); $("issueSearch").focus(); }
});
$("userBtn").onclick = () => { const m = $("userMenu"); m.hidden = !m.hidden; $("userBtn").setAttribute("aria-expanded", String(!m.hidden)); };
$("projSearch").oninput = renderProjects;
$("issueSearch").oninput = e => { S.q = e.target.value; renderIssues(); };
$("btnProj").onclick = () => { S.projects = null; loadProjects(); };

$("btnLogin").onclick = async () => {
  const b = $("btnLogin"), err = $("signinErr"); b.disabled = true; err.hidden = true;
  try {
    const r = await fetch("/api/login", {method: "POST"}); const j = await r.json();
    if (!r.ok) throw new Error(j.error);
    location.href = j.url;
  } catch (e) { err.textContent = e.message; err.hidden = false; b.disabled = false; }
};

$("btnConnect").onclick = async () => {
  const b = $("btnConnect"), err = $("connectErr"); b.disabled = true; err.hidden = true;
  try {
    const r = await api("connect_jira");
    if (r.url) location.href = r.url; else { await refreshMe(); }
  } catch (e) { err.textContent = e.message; err.hidden = false; b.disabled = false; }
};

$("btnNew").onclick = () => { if (S.project) $("nProj").value = S.project; $("dlgNew").showModal(); $("nSum").focus(); };
$("nCancel").onclick = () => $("dlgNew").close();
$("formNew").onsubmit = async ev => {
  ev.preventDefault(); const b = $("nSubmit"); b.disabled = true;
  try {
    const r = await api("create_task", {project: $("nProj").value, summary: $("nSum").value, description: $("nDesc").value, issue_type: $("nType").value});
    toast(`Created <a href="${esc(r.url)}" target="_blank" rel="noopener noreferrer">${esc(r.key)}</a>`, "ok", true);
    const inCurrent = $("nProj").value === S.project;
    const target = $("nProj").value;
    $("dlgNew").close(); $("formNew").reset();
    if (inCurrent) loadIssues(); else selectProject(target);
  } catch (e) { toast(e.message, "error"); } finally { b.disabled = false; }
};

document.addEventListener("submit", async ev => {
  if (ev.target.id !== "editForm") return;
  ev.preventDefault();
  const f = ev.target, key = f.dataset.key, btn = f.querySelector("button[type=submit]"); btn.disabled = true;
  const body = {issue_key: key, summary: f.summary.value, description: f.description.value, status: f.status.value};
  try {
    await api("update_issue", body); toast(`${key} updated.`);
    closeDrawer(); loadIssues();
  } catch (e) { toast(e.message, "error"); btn.disabled = false; }
});

/* ---------- boot ---------- */
async function refreshMe() {
  try {
    const r = await fetch("/api/me"); const j = await r.json();
    if (!r.ok) throw new Error(j.error);
    S.me = j.signed_in ? j.me : null;
  } catch (e) { S.me = null; show("signin"); $("signinErr").textContent = e.message; $("signinErr").hidden = false; return; }
  route();
}
(async () => {
  const err = new URLSearchParams(location.search).get("error");
  if (err) { history.replaceState(null, "", "/"); toast(err, "error"); }
  await refreshMe();
})();
