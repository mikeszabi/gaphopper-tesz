// Gaphopper webes felület: egyetlen oldal, keretrendszer nélkül. Az adatokat a /api végpontok adják.
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (n) => (n == null ? "–" : Number(n).toLocaleString("hu-HU"));
const usd = (n) => (n == null ? "–" : "$" + Number(n).toFixed(n < 1 ? 4 : 2));

async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", headers: {} };
  if (opts.body !== undefined) {
    if (opts.raw) init.body = opts.body;
    else { init.body = JSON.stringify(opts.body); init.headers["Content-Type"] = "application/json"; }
  }
  const r = await fetch("/api/" + path, init);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}

function toast(msg, err = false) {
  const t = $("#toast");
  t.textContent = msg; t.className = "toast" + (err ? " err" : ""); t.hidden = false;
  clearTimeout(toast.h); toast.h = setTimeout(() => (t.hidden = true), err ? 7000 : 3500);
}
async function act(fn, okMsg) {
  try { const r = await fn(); if (okMsg) toast(typeof okMsg === "function" ? okMsg(r) : okMsg); return r; }
  catch (e) { toast(e.message, true); throw e; }
}

const STATE = {
  done: ["✓", "kész"], skip: ["↷", "kihagyva"], error: ["✕", "hiba"], wait: ["·", "vár"], run: ["", "fut"],
  review: ["!", "szakértőre vár"], na: ["–", "nem kell"], part: ["◐", "részben"],
  unknown: ["?", "nem látható"], in: ["✓", "a gráfban"], out: ["–", "nincs a gráfban"], missing: ["!", "hiányzik"],
};
const STATUS_HU = {
  NYERS: "feldolgozásra vár", P1_KESZ: "felbontva, bírálatra vár",
  P2_ELFOGADVA: "elfogadva, gráfba írható", SZAKERTORE: "szakértőre vár", GRAFBAN: "a gráfban", ELVETVE: "elvetve", HIBA: "hiba",
};
const pill = (state, label) => {
  const [ic, def] = STATE[state] || ["", state];
  return `<span class="pill s-${state}">${state === "run" ? '<span class="dot"></span>' : esc(ic)} ${esc(label ?? def)}</span>`;
};

// ------------------------------------------------------------------ útválasztás és élő jelzés
const pages = {};
let current = null, timer = null;
function route() {
  const tab = (location.hash.slice(1) || "futas").split("/")[0];
  $$("nav a").forEach((a) => a.classList.toggle("on", a.dataset.tab === tab));
  clearInterval(timer); timer = null;
  closeDrawer();
  current = tab;
  (pages[tab] || pages.futas)($("#view"));
}
window.addEventListener("hashchange", route);

async function refreshLive() {
  try {
    const { tasks } = await api("tasks");
    const run = tasks.filter((t) => t.status === "fut");
    const el = $("#live");
    el.hidden = !run.length;
    if (run.length) {
      el.textContent = run.map((t) => t.label).join(" · ");
      el.onclick = () => (location.hash = "#futas");
    }
  } catch { /* a szerver épp újraindul */ }
}
setInterval(refreshLive, 3000);

// ------------------------------------------------------------------ KOMPONENSEK
pages.komponensek = async (view) => {
  view.innerHTML = `<h1>Komponensek állapota</h1>
    <p class="sub">A mentett beállításokkal ellenőrzi a szolgáltatásokat. Az ellenőrzés a webes szerver gépéről fut.</p>
    <div class="controls"><button id="component-refresh">Állapot frissítése</button>
    <button id="neo4j-start">Neo4j Docker indítása</button><a href="#modellek">Modellbeállítások</a></div>
    <p id="component-message" role="status"></p><div id="component-results" aria-live="polite"></div>`;
  const refresh = $("#component-refresh", view), start = $("#neo4j-start", view);
  const message = $("#component-message", view), results = $("#component-results", view);
  async function check() {
    refresh.disabled = start.disabled = true;
    results.textContent = "Komponensek ellenőrzése…";
    try {
      const data = await api("components");
      results.innerHTML = data.components.map(c => `<div class="card"><h2>${esc(c.name)} ${pill(c.state)}</h2>
        <p class="muted">${esc(c.target)}</p><p>${esc(c.detail)}</p></div>`).join("") +
        `<p class="muted">Ellenőrizve: ${esc(new Date(data.checked_at).toLocaleString("hu-HU"))}</p>`;
    } catch(e) { results.textContent = e.message; }
    finally { refresh.disabled = start.disabled = false; }
  }
  refresh.onclick = check;
  start.onclick = async () => {
    refresh.disabled = start.disabled = true;
    message.textContent = "Neo4j-konténer indítása…";
    try {
      const r = await api("components/neo4j-start", {method: "POST", body: {}});
      message.textContent = r.ok ? r.message : r.error;
      await check();
    } catch(e) { message.textContent = e.message; }
    finally { refresh.disabled = start.disabled = false; }
  };
  await check();
};

// ------------------------------------------------------------------ FUTÁS
const ui = { sel: new Set(), filter: "", phaseFilter: "", scope: "next", limit: 5, until: "graph", workers: 4, force: false, dry: false, task: null, open: null };

pages.futas = async (view) => {
  view.innerHTML = `
    <h1>Futás</h1>
    <p class="sub">Munkakörönként látod, melyik fázis készült el, és mi került a neo4j-gráfba. A lista magától frissül.</p>
    <div id="flow" class="flow"></div>
    <div id="usage-total" class="card"></div><details id="costd"><summary>Tokenek, idő és költség lépésenként</summary><div id="cost" class="card" style="margin-top:6px"></div></details>
    <div class="row" style="margin-top:16px">
      <div class="card" style="flex:1 1 460px"><h2>Indítás</h2><div id="ctl"></div></div>
      <div class="card" style="flex:1 1 460px"><h2>Futó és legutóbbi parancs</h2><div id="task"></div></div>
    </div>
    <div class="card">
      <div class="controls" style="margin-bottom:10px">
        <h2 style="margin:0 auto 0 0">Munkakörök <span id="cnt" class="muted small"></span></h2>
        <button id="clear-selection" disabled>Kijelölés törlése</button>
        <input id="q" placeholder="Keresés címre vagy azonosítóra" value="${esc(ui.filter)}" style="width:260px">
        <select id="pf">
          <option value="">mind</option><option value="run">most fut</option><option value="error">hibás</option>
          <option value="review">szakértőre vár</option><option value="wait">még nincs kész</option>
          <option value="in">a gráfban</option><option value="out">nincs a gráfban</option>
          <option value="missing">eltérés a gráffal</option><option value="p5">rubrika hiányzik</option>
        </select>
      </div>
      <div id="jobs"></div>
    </div>`;
  $("#clear-selection").onclick = () => { ui.sel.clear(); drawJobs(); scopeInfo(); };
  $("#q").oninput = (e) => { ui.filter = e.target.value; drawJobs(); };
  $("#pf").value = ui.phaseFilter;
  $("#pf").onchange = (e) => { ui.phaseFilter = e.target.value; drawJobs(); };
  drawControls();
  await loadRun();
  timer = setInterval(loadRun, 3000);
};

let runData = { jobs: [], overview: null };
async function loadRun() {
  if (current !== "futas") return;
  try {
    const [jobs, ov, tl] = await Promise.all([api("jobs"), api("overview"), api("tasks")]);
    runData = { jobs: jobs.jobs, overview: ov, graph_ok: jobs.graph_ok, graph_err: jobs.graph_err, tasks: tl.tasks };
    drawFlow(); drawCost(); drawJobs(); drawTask();
    if (ui.open) refreshDrawer();
  } catch (e) { $("#jobs").innerHTML = `<p class="bad">${esc(e.message)}</p>`; }
}

function drawFlow() {
  const ov = runData.overview;
  const box = (p) => {
    const c = ov.counts[p.key] || {};
    const order = ["run", "done", "skip", "part", "review", "error", "wait", "na", "unknown"];
    const pills = order.filter((s) => c[s]).map((s) => pill(s, `${STATE[s][1]}: ${c[s]}`)).join("");
    return `<div class="step"><div class="k">${p.key}</div><div class="n">${esc(p.name)}</div>
      <div class="a">${esc(p.about)}</div><div class="c">${pills || '<span class="muted small">még nincs adat</span>'}</div></div>`;
  };
  const db = ov.db;
  const dbBox = `<div class="step db"><div class="k">neo4j</div><div class="n">Adatbázis</div>
    <div class="a">${ov.graph_ok ? "munkakörök a gráfban" : "nem érhető el: " + esc(ov.graph_err)}</div>
    <div class="c">${ov.graph_ok ? [pill("in", `benne: ${db.in || 0}`), pill("out", `nincs benne: ${db.out || 0}`),
      db.missing ? pill("missing", `eltérés: ${db.missing}`) : ""].join("") : pill("unknown")}</div></div>`;
  const ph = ov.phases.map(box);
  $("#flow").innerHTML = [ph[0], ph[1], ph[2], ph[3], dbBox, ph[4]].join('<div class="arrow">→</div>');
}

function drawCost() {
  const rows = runData.overview.by_step;
  const t = runData.overview.usage_totals;
  $("#usage-total").textContent = `Összes naplózott hívás: ${fmt(t.hivas)} · Bemenet: ${fmt(t.input)} · Cache olvasás: ${fmt(t.cache_read)} · Kimenet: ${fmt(t.output)} token · Árazott fogyasztás: ${usd(t.known_cost)}${t.unpriced_calls ? ` · ${t.unpriced_calls} hívás ára ismeretlen` : ""}. Becsült API-költség a beállított egységárakkal.`;
  $("#cost").innerHTML = rows.length ? `<table><thead><tr><th>Lépés</th><th>Munkakör</th><th>Hívás</th><th>Bemenet</th><th>Kimenet</th><th>Idő</th><th>Költség</th><th>Munkakörönként</th></tr></thead><tbody>
    ${rows.map((r) => `<tr><td><b>${esc(r.step)}</b></td><td>${fmt(r.munkakor_db)}</td><td>${fmt(r.hivas)}${r.hivas !== r.sikeres ? ` <span class="bad small">(${r.hivas - r.sikeres} sikertelen)</span>` : ""}</td>
      <td>${fmt(r.input)}</td><td>${fmt(r.output)}</td><td>${r.ido_mp} mp</td><td>${usd(r.koltseg_usd)}</td><td>${usd(r.koltseg_per_munkakor)}</td></tr>`).join("")}</tbody></table>`
    : '<p class="muted">Még nem volt modellhívás.</p>';
}

function drawControls() {
  const seg = (name, opts) => `<div class="seg" data-k="${name}">${opts.map(([v, l]) =>
    `<button data-v="${v}" class="${ui[name] === v ? "on" : ""}">${l}</button>`).join("")}</div>`;
  $("#ctl").innerHTML = `
    <div class="controls">
      <label class="f">Mire?${seg("scope", [["sel", "kijelöltek"], ["next", "következő N várakozó"], ["all", "minden várakozó"]])}</label>
      <label class="f">N<input id="lim" type="number" min="1" value="${ui.limit}" style="width:70px"></label>
    </div>
    <div class="controls" style="margin-top:10px">
      <label class="f">Meddig?${seg("until", [["p1", "P1"], ["p2", "P1–P2"], ["graph", "gráfba írásig"]])}</label>
      <label class="f">Párhuzamos hívás<input id="wk" type="number" min="1" max="32" value="${ui.workers}" style="width:70px"></label>
      <label class="f" title="A már kész munkaköröket is elölről futtatja">Kész is újra<span><input id="frc" type="checkbox" ${ui.force ? "checked" : ""}></span></label>
      <label class="f" title="P1–P3 lefut (tokenek, költség), de sem a gráf, sem a state.sqlite nem változik; eredmény: runs/dryrun/">Próbafutás (dry run)<span><input id="dry" type="checkbox" ${ui.dry ? "checked" : ""}></span></label>
    </div>
    <div class="controls" style="margin-top:14px">
      <button class="primary" id="go">${ui.dry ? "Próbafutás indítása" : "Feldolgozás indítása"}</button>
      <button id="p5" title="Igazolási mondat és rubrika a gráf rubrika nélküli TESZ-eihez">Tanúsítás (P5)</button>

    </div>
    <p class="muted small" id="scopeinfo" style="margin:10px 0 0"></p>`;
  $$("#ctl .seg").forEach((s) => s.onclick = (e) => {
    const b = e.target.closest("button"); if (!b) return;
    ui[s.dataset.k] = b.dataset.v; drawControls();
  });
  $("#lim").onchange = (e) => { ui.limit = +e.target.value; scopeInfo(); };
  $("#wk").onchange = (e) => (ui.workers = +e.target.value);
  $("#frc").onchange = (e) => (ui.force = e.target.checked);
  $("#dry").onchange = (e) => { ui.dry = e.target.checked; drawControls(); };
  $("#go").onclick = startRun;
  $("#p5").onclick = () => act(() => api("certify", { method: "POST", body: {} }), "A tanúsítás elindult.").then(loadRun);
  scopeInfo();
}
function scopeInfo() {
  const el = $("#scopeinfo"); if (!el) return;
  el.textContent = (ui.scope === "sel" ? `Kijelölve: ${ui.sel.size} munkakör (a táblázat jelölőnégyzeteivel).`
    : ui.scope === "next" ? `A még nem kész munkakörök közül az első ${ui.limit}.` : "Minden munkakör, amely még nincs a gráfban és nem elvetett.")
    + (ui.dry ? " Próbafutás: a „Meddig?” és a „Kész is újra” nem számít; az eredmény a munkakör részleteinél látszik." : "");
  $("#go").disabled = ui.scope === "sel" && !ui.sel.size;
}
async function startRun() {
  const body = { until: ui.until, workers: ui.workers, force: ui.force, dry_run: ui.dry };
  if (ui.scope === "sel") body.jobs = [...ui.sel];
  if (ui.scope === "next") body.limit = ui.limit;
  const r = await act(() => api("run", { method: "POST", body }), ui.dry ? "A próbafutás elindult." : "A feldolgozás elindult.");
  ui.task = r.task; loadRun(); refreshLive();
}

function drawTask() {
  const list = runData.tasks || [];
  const el = $("#task");
  if (!list.length) { el.innerHTML = '<p class="muted">Még nem indítottál parancsot a felületről.</p>'; return; }
  const t = list.find((x) => x.id === ui.task) || list.find((x) => x.status === "fut") || list[0];
  const opts = list.map((x) => `<option value="${x.id}" ${x.id === t.id ? "selected" : ""}>${esc(x.started.slice(5, 16).replace("T", " "))} · ${esc(x.label)} · ${esc(x.status)}</option>`).join("");
  const keep = $("#tlog")?.scrollTop;
  el.innerHTML = `<div class="controls" style="margin-bottom:8px"><select id="tsel" style="flex:1">${opts}</select>
      ${t.status === "fut" ? '<button class="danger" id="tstop">Leállítás</button>' : ""}</div>
    <div style="margin-bottom:6px">${t.status === "fut" ? pill("run", "fut") : t.status === "kész" ? pill("done", "befejeződött") : pill("error", t.status)}
      <span class="muted small">indítva ${esc(t.started.replace("T", " "))}${t.ended ? ", vége " + esc(t.ended.slice(11)) : ""}</span></div>
    <p id="terr" class="bad small" hidden></p><pre id="tlog" class="log">betöltés…</pre>`;
  $("#tsel").onchange = (e) => { ui.task = e.target.value; drawTask(); };
  if ($("#tstop")) $("#tstop").onclick = () => act(() => api(`tasks/${t.id}/stop`, { method: "POST" }), "Leállítva.").then(loadRun);
  api(`tasks/${t.id}?lines=80`).then((d) => {
    const pre = $("#tlog"); if (!pre) return;
    pre.textContent = d.log || "(még nincs kimenet)";
    if (t.status === "hiba" || t.status === "megszakadt") {
      const last = (d.log || "").trim().split("\n").filter((l) => l.trim()).pop() || "";
      $("#terr").hidden = false;
      $("#terr").textContent = t.status === "megszakadt" ? "A parancs váratlanul leállt (pl. a felület újraindult)." : "A parancs hibával állt le: " + last;
    }
    pre.scrollTop = t.status === "fut" || keep == null ? pre.scrollHeight : keep;
  });
}

function matches(j) {
  const q = ui.filter.toLowerCase();
  if (q && !(j.job_id + " " + j.title).toLowerCase().includes(q)) return false;
  const f = ui.phaseFilter, st = j.phases.map((p) => p.state);
  if (!f) return true;
  if (f === "run" || f === "error" || f === "review") return st.includes(f);
  if (f === "wait") return j.status !== "GRAFBAN" && j.status !== "ELVETVE";
  if (f === "p5") return j.db.state === "in" && ["wait", "part"].includes(j.phases[4].state);
  return j.db.state === f;
}

function drawJobs() {
  const rows = runData.jobs.filter(matches);
  $("#cnt").textContent = `${rows.length} / ${runData.jobs.length} · kijelölve: ${ui.sel.size}`;
  $("#clear-selection").disabled = ui.sel.size === 0;
  if (!runData.jobs.length) {
    $("#jobs").innerHTML = '<p class="muted">Még nincs munkakör. A <a href="#feltoltes">Feltöltés</a> oldalon tölthetsz be rekordokat vagy dokumentumokat.</p>';
    return;
  }
  const cell = (p) => `<td class="ph" title="${esc(p.text)}">${pill(p.state)}<span class="cell">${esc(p.text)}</span></td>`;
  const shown = rows.slice(0, 500);
  $("#jobs").innerHTML = `<div style="overflow-x:auto"><table><thead><tr><th><input type="checkbox" id="all"></th><th>Munkakör</th>
    <th>E0 Kinyerés</th><th>P1 Felbontás</th><th>P2 Bírálat</th><th>P3 Gráfba írás</th><th>Adatbázis</th><th>P5 Tanúsítás</th></tr></thead>
    <tbody>${shown.map((j) => `<tr class="click ${ui.open === j.job_id ? "sel" : ""}" data-id="${esc(j.job_id)}">
      <td><input type="checkbox" class="pick" ${ui.sel.has(j.job_id) ? "checked" : ""}></td>
      <td class="jt"><b>${esc(j.title || j.job_id)}</b><div class="muted small mono">${esc(j.job_id)}</div></td>
      ${cell(j.phases[0])}${cell(j.phases[1])}${cell(j.phases[2])}${cell(j.phases[3])}
      <td class="ph" title="${esc(j.db.text)}">${pill(j.db.state)}<span class="cell">${esc(j.db.text)}</span></td>
      ${cell(j.phases[4])}</tr>`).join("")}</tbody></table></div>
    ${rows.length > shown.length ? `<p class="muted small">Az első ${shown.length} látszik; szűkíts kereséssel.</p>` : ""}`;
  const selectedCount = rows.filter(j => ui.sel.has(j.job_id)).length;
  $("#all").checked = rows.length > 0 && selectedCount === rows.length;
  $("#all").indeterminate = selectedCount > 0 && selectedCount < rows.length;
  $("#all").setAttribute("aria-label", "A szűrt munkakörök kijelölése vagy kijelölésük törlése");
  $("#all").onchange = (e) => { rows.forEach((j) => e.target.checked ? ui.sel.add(j.job_id) : ui.sel.delete(j.job_id)); drawJobs(); scopeInfo(); };
  $$("#jobs tbody tr").forEach((tr) => {
    tr.onclick = (e) => {
      const id = tr.dataset.id;
      if (e.target.classList.contains("pick")) {
        e.target.checked ? ui.sel.add(id) : ui.sel.delete(id);
        if (ui.sel.size && ui.scope !== "sel") { ui.scope = "sel"; drawControls(); } else scopeInfo();
        return;
      }
      openJob(id);
    };
  });
}

// ------------------------------------------------------------------ egy munkakör részletei
function closeDrawer() { ui.open = null; $("#drawer").hidden = true; }
async function openJob(id) { ui.open = id; $$("#jobs tr").forEach((tr) => tr.classList.toggle("sel", tr.dataset.id === id)); await refreshDrawer(true); }
let drawerSig = "";
async function refreshDrawer(force = false) {
  const id = ui.open; if (!id) return;
  let d;
  try { d = await api("jobs/" + encodeURIComponent(id)); } catch (e) { toast(e.message, true); return; }
  const sig = JSON.stringify([d.status, d.phases, d.db, d.usage, d.queue.length, d.approval_tasks, d.dryrun && d.dryrun.file_time]);
  if (!force && sig === drawerSig) return;
  drawerSig = sig;
  const el = $("#drawer"); const scroll = el.scrollTop; el.hidden = false;
  const names = { E0: "Kinyerés dokumentumból", P1: "Felbontás TESZ-nivalókra és SZÍN-ekre", P2: "Bírálat", P3: "Egyesítés és gráfba írás", P5: "Tanúsítás (igazolás, rubrika)" };
  const useMeta = (k) => {
    const u = d.usage[k]; if (!u) return "";
    return `<div class="meta">${esc(u.model)} · ${u.calls} hívás${u.calls !== u.ok ? ` (${u.calls - u.ok} sikertelen)` : ""} ·
      be ${fmt(u.input)} / ki ${fmt(u.output)} token · ${u.seconds} mp · ${usd(u.cost)} · ${esc(u.last.replace("T", " ").slice(0, 16))}</div>`;
  };
  const tl = d.phases.map((p) => {
    const [ic] = STATE[p.state] || ["?"];
    let extra = "";
    if (p.key === "P2" && d.p2 && d.p2.changes.length)
      extra = `<details><summary>${d.p2.changes.length} módosítás</summary><table>${d.p2.changes.map((c) =>
        `<tr><td class="mono small">${esc(c.op)}</td><td class="mono small">${esc(c.cand_id || "")}${c.into ? " → " + esc(c.into) : ""}</td><td class="small">${esc(c.reason_hu || "")}</td></tr>`).join("")}</table></details>`;
    if (p.key === "P1" && d.errors.P1) extra = `<pre>${esc(d.errors.P1)}</pre>`;
    if (p.key === "P2" && d.errors.P2) extra = `<pre>${esc(d.errors.P2)}</pre>`;
    return `<li><div class="ic s-${p.state}">${p.state === "run" ? '<span class="dot" style="width:8px;height:8px;border-radius:50%;background:var(--run);animation:pulse 1s infinite"></span>' : esc(ic)}</div>
      <div><div class="t">${p.key} · ${esc(names[p.key])} ${pill(p.state)}</div><div>${esc(p.text)}</div>${useMeta(p.key)}${extra}</div></li>`;
  }).join("");
  const graphHead = !d.graph_ok ? pill("unknown", "a neo4j nem érhető el") : d.db.state === "in"
    ? pill("in", `${d.graph.length} TESZ kapcsolódik ehhez a munkakörhöz`) : pill(d.db.state, d.db.text);
  const teszRow = (t) => `<tr><td>${t.alap_szin ? "●" : "○"} <b>${esc(t.label_hu)}</b><div class="muted small">${esc(t.kozeg)} / ${esc(t.helyzet)} · EQF ${esc(t.eqf)}${t.alap_szin ? " · ALAP-SZÍN" : " · " + esc(t.szin_kulonbseg_hu || "")}</div></td>
    <td class="ph">${t.in_graph === true ? pill("in", "bekerült") : t.in_graph === null ? pill("unknown") : pill("out", "nincs benne")}
    ${t.decision ? `<span class="cell">${esc(t.decision)}</span>` : ""}${t.graph_id ? `<span class="cell mono">${esc(t.graph_id)}</span>` : ""}</td></tr>`;
  const magHead = (m) => `<tr class="mag"><td colspan="2"><b>${esc(m.nivalo_id)} · ${esc(m.label_hu)}</b><div class="muted small">${esc(m.tesz_type)} · ‹${esc(m.szer_elem)}› · ${esc(m.relevance)} · ${esc(m.kor_id || "")}
    ${(m.melyites_jeloltek || []).map((x) => ` · ◇ mélyítési jelölt: ${esc(x.kozeg)}/${esc(x.helyzet)}`).join("")}</div></td></tr>`;
  const tesz = (d.nivalok && d.nivalok.length)
    ? d.nivalok.map((m) => magHead(m) + d.szinek.filter((t) => t.nivalo_id === m.nivalo_id).map(teszRow).join("")).join("")
    : d.szinek.map(teszRow).join("");
  const dr = d.dryrun;
  const dryHtml = !dr ? "" : `<details ${ui.openDry ? "open" : ""} id="dryd"><summary>Próbafutás eredménye (${esc((dr.file_time || "").replace("T", " "))}) – ${dr.ok
      ? `${dr.szamok.nivalo} TESZ-nivaló / ${dr.szamok.szin} TESZ-SZÍN, ${dr.szamok.melyitesi_jelolt} mélyítési jelölt` : "hiba"} · ${fmt(dr.total.input)} be / ${fmt(dr.total.output)} ki token · ${usd(dr.total.koltseg_usd)} · ${dr.total.ido_mp} mp</summary>
    ${dr.ok ? "" : `<pre>${esc(dr.error || "")}</pre>`}
    <p class="small">${(dr.rule_flags || []).length ? (dr.rule_flags || []).map((f) => `⚑ ${esc(f.rule)}: ${esc(f.message)}`).join("<br>") : "Szabályjelzés nincs."}</p>
    ${dr.ok ? `<table><thead><tr><th>TESZ-nivaló / TESZ-SZÍN</th><th>SZÍN</th><th>egyesítés</th></tr></thead><tbody>${dr.nivalok.map((m) =>
      `<tr class="mag"><td><b>${esc(m.nivalo_id)} · ${esc(m.label_hu)}</b><div class="muted small">${esc(m.tesz_type)} · ‹${esc(m.primary_szer)}› · ${esc(m.relevance)}</div></td><td></td><td class="small">${esc(m.egyesites || "")}</td></tr>`
      + m.szinek.map((v) => `<tr><td>${v.alap_szin ? "●" : "○"} ${esc(v.alap_szin ? "ALAP-SZÍN" : v.label_hu)}<div class="muted small">${esc(v.szin_kulonbseg_hu || "")}</div></td><td class="small mono">${esc(v.kozeg)}/${esc(v.helyzet)}</td><td class="small">${esc(v.egyesites || "")}</td></tr>`).join("")
      + (m.melyites_jeloltek || []).map((x) => `<tr><td class="small">◇ mélyítési jelölt: ${esc(x.indok_hu || "")}</td><td class="small mono">${esc(x.kozeg)}/${esc(x.helyzet)}</td><td></td></tr>`).join("")).join("")}</tbody></table>` : ""}
    <table><thead><tr><th>Lépés</th><th>Modell</th><th>Bemenet</th><th>Kimenet</th><th>Idő</th><th>Költség</th></tr></thead><tbody>${(dr.usage || []).map((u) =>
      `<tr><td>${esc(u.step)}</td><td class="small">${esc(u.model)}</td><td>${fmt(u.input + u.cache_read + u.cache_write)}</td><td>${fmt(u.output)}</td><td>${u.ido_mp.toFixed(1)} mp</td><td>${usd(u.koltseg_usd)}</td></tr>`).join("")}</tbody></table></details>`;
  const dropped = d.dropped.map((t) => `<tr><td><s>${esc(t.label_hu)}</s><div class="muted small">${esc(t.reason || "")}</div></td><td>${pill("skip", "P2 kivette")}</td></tr>`).join("");
  const approval = (d.approval_tasks || [])[0];
  const approving = approval?.status === "fut";
  const queue = d.queue.map((q) => `<div class="card" style="border-color:var(--warn)"><b>Szakértői döntés kell</b> <span class="tag">${esc(q.kind)}</span>
    ${(q.payload.flags || []).map((f) => `<div class="small">⚑ ${esc(f.rule)}: ${esc(f.message || f.detail || "")}</div>`).join("")}
    ${(q.payload.questions || []).map((x) => `<div class="small">❓ ${esc(x)}</div>`).join("")}
    ${(q.payload.items || []).map((x) => `<div class="small">↔ ${esc(x.label)} (${esc(x.id)})</div>`).join("")}
    <div class="controls" style="margin-top:8px">${q.kind === "job_review"
      ? `<button class="primary" data-q="${q.id}" data-a="approve" ${approving ? "disabled" : ""}>${approving ? "Gráfba írás folyamatban…" : "Jóváhagyás és gráfba írás"}</button><button class="danger" data-q="${q.id}" data-a="reject" ${approving ? "disabled" : ""}>Elutasítás</button>`
      : `<button data-q="${q.id}" data-a="close">Rendben, lezárom</button>`}</div></div>`).join("");
  el.innerHTML = `<button class="close" id="dx">Bezárás ✕</button>
    <h1>${esc(d.title || d.job_id)}</h1>
    <p class="sub"><span class="mono">${esc(d.job_id)}</span> · ${esc(STATUS_HU[d.status] || d.status)}${d.note ? " · " + esc(d.note) : ""}</p>
    <div class="controls" style="margin-bottom:16px">
      <button class="primary" id="rerun">Újrafuttatás gráfba írásig</button>
      <button id="reset" title="A következő indításkor elölről dolgozza fel">Vissza a várakozók közé</button>
      <button id="dryone" title="P1–P3 lefut, de sem a gráf, sem a state.sqlite nem változik">Próbafutás</button>
    </div>
    ${approval ? `<div role="status" class="card">${approving ? "Jóváhagyás és gráfba írás folyamatban. A P3 modell válaszára is várhat; a futás naplója a főoldalon követhető." : approval.status === "kész" ? "A jóváhagyás és gráfba írás befejeződött." : "A gráfba írás nem fejeződött be. A szakértői kérés nyitva maradt."}${approval.log ? `<pre>${esc(approval.log)}</pre>` : ""}</div>` : ""}
    ${queue}
    <h2>Fázisok</h2><ul class="tl">${tl}</ul>
    <h2>Mi került az adatbázisba?</h2>
    <p>${graphHead} ${d.db.graph_id && d.db.graph_id !== d.job_id ? `<span class="muted small">a gráfban ezen az azonosítón: <span class="mono">${esc(d.db.graph_id)}</span></span>` : ""}</p>
    ${dryHtml}
    ${d.szinek.length ? `<table><thead><tr><th>Végleges TESZ-nivalók és TESZ-SZÍN-eik (● ALAP-SZÍN, ○ mélyítő/átvivő)</th><th>A gráfban</th></tr></thead><tbody>${tesz}${dropped}</tbody></table>`
      : '<p class="muted">Még nincs végleges TESZ-lista (a felbontás nem futott le).</p>'}
    ${d.graph && d.graph.length ? `<details><summary>A gráf jelenlegi tartalma ennél a munkakörnél (${d.graph.length})</summary><table><thead><tr><th>TESZ</th><th>típus</th><th>rubrika</th><th>munkakör</th></tr></thead><tbody>
      ${d.graph.map((g) => `<tr><td>${esc(g.label_hu)}<div class="mono small muted">${esc(g.id)}</div></td><td class="small">${esc(g.tesz_type)}</td><td>${g.rubrika ? pill("done", g.rubrika + " pont") : pill("wait", "nincs")}</td><td class="small">${g.munkakor_db}</td></tr>`).join("")}</tbody></table></details>` : ""}
    <details><summary>A beolvasott rekord</summary><pre>${esc(JSON.stringify(d.record, null, 1))}</pre></details>`;
  el.scrollTop = scroll;
  $("#dx").onclick = () => { closeDrawer(); drawJobs(); };
  $("#rerun").onclick = () => act(() => api("run", { method: "POST", body: { jobs: [d.job_id], until: "graph", workers: 1, force: true } }),
    "Elindult az újrafuttatás.").then((r) => { ui.task = r.task; loadRun(); });
  $("#dryone").onclick = () => act(() => api("run", { method: "POST", body: { jobs: [d.job_id], dry_run: true, workers: 1 } }),
    "Elindult a próbafutás; az eredmény itt jelenik meg.").then((r) => { ui.task = r.task; ui.openDry = true; loadRun(); });
  if ($("#dryd")) $("#dryd").ontoggle = (e) => (ui.openDry = e.target.open);
  $("#reset").onclick = () => act(() => api(`jobs/${encodeURIComponent(d.job_id)}/reset`, { method: "POST" }), "Visszaállítva.").then(loadRun);
  $$("[data-q]", el).forEach((b) => b.onclick = async () => {
    const action = b.dataset.a, qid = b.dataset.q;
    b.disabled = true;
    b.textContent = action === "approve" ? "Jóváhagyás indítása…" : "Feldolgozás…";
    try {
      const r = await api(`review/${qid}/${action}`, { method: "POST" });
      if (r.task) { ui.task = r.task; toast("A jóváhagyás és gráfba írás elindult."); }
      else toast("Kész.");
      await loadRun(); await refreshDrawer(true); refreshLive();
    } catch(e) {
      toast(e.message, true);
      b.parentElement.insertAdjacentHTML("beforeend", `<p role="alert" class="bad">${esc(e.message)}</p>`);
      b.disabled = false;
      b.textContent = action === "approve" ? "Jóváhagyás és gráfba írás" : action === "reject" ? "Elutasítás" : "Rendben, lezárom";
    }
  });
}

// ------------------------------------------------------------------ ADATBÁZIS
pages.adatbazis = async (view) => {
  view.innerHTML = `<h1>Adatbázis</h1><p class="sub">Mi van a futási naplóban (SQLite) és mi a neo4j-gráfban, és hol tér el a kettő.</p><div id="dbv">betöltés…</div>`;
  let d;
  try { d = await api("db"); } catch (e) { $("#dbv").innerHTML = `<p class="bad">${esc(e.message)}</p>`; return; }
  const st = d.sqlite.jobs.map((r) => `<tr><td>${esc(STATUS_HU[r.status] || r.status)}</td><td class="mono small muted">${esc(r.status)}</td><td style="text-align:right"><b>${fmt(r.db)}</b></td></tr>`).join("");
  const tbl = Object.entries(d.sqlite.tables).map(([k, v]) => `<tr><td class="mono">${k}</td><td style="text-align:right">${fmt(v)}</td></tr>`).join("");
  let g = `<p>${pill("unknown", "nem érhető el")} <span class="muted">${esc(d.neo4j_uri)}: ${esc(d.graph_err)}</span></p>
    <p class="muted small">Indítsd el a neo4j-t, és ellenőrizd a .env NEO4J_* beállításait.</p>`;
  let cmp = "";
  if (d.graph_ok) {
    g = `<p>${pill("done", "kapcsolódva")} <span class="muted mono small">${esc(d.neo4j_uri)}</span></p>
      <table><thead><tr><th>Csomópont</th><th style="text-align:right">db</th></tr></thead><tbody>
      ${d.labels.map((r) => `<tr><td>${esc(r.tipus)}</td><td style="text-align:right">${fmt(r.db)}</td></tr>`).join("")}</tbody></table>
      <details><summary>Kapcsolatok</summary><table>${d.rels.map((r) => `<tr><td class="mono small">${esc(r.kapcsolat)}</td><td style="text-align:right">${fmt(r.db)}</td></tr>`).join("")}</table></details>`;
    const c = d.compare;
    cmp = `<div class="card"><h2>Összevetés</h2><div class="kpis">
      <div><div class="big ok">${fmt(c.in)}</div><div class="muted">munkakör a gráfban</div></div>
      <div><div class="big">${fmt(c.out)}</div><div class="muted">munkakör még nincs a gráfban</div></div>
      <div><div class="big ${c.missing.length ? "bad" : ""}">${fmt(c.missing.length)}</div><div class="muted">kész az állapot szerint, de hiányzik a gráfból</div></div>
      <div><div class="big ${c.orphans.length ? "bad" : ""}">${fmt(c.orphans.length)}</div><div class="muted">a gráfban van, a futási naplóban nincs</div></div>
      <div><div class="big">${fmt(d.no_rubric)}</div><div class="muted">TESZ rubrika nélkül (P5 hiányzik)</div></div></div>
      ${c.missing.length ? `<p class="small">Hiányzik: ${c.missing.map((x) => `<span class="mono">${esc(x)}</span>`).join(", ")}. A <a href="#futas">Futás</a> oldalon újrafuttathatók.</p>` : ""}
      ${c.orphans.length ? `<details><summary>Csak a gráfban lévő munkakörök</summary><p class="small">${c.orphans.map((o) => `${esc(o.label)} <span class="mono muted">${esc(o.id)}</span>`).join("<br>")}</p></details>` : ""}
      </div>`;
  }
  $("#dbv").innerHTML = `${cmp}<div class="row">
    <div class="card"><h2>Futási napló (SQLite)</h2><p class="muted small mono">${esc(d.sqlite_path)}</p>
      <table><thead><tr><th>Munkakörök állapota</th><th></th><th style="text-align:right">db</th></tr></thead><tbody>${st || '<tr><td class="muted">nincs munkakör</td></tr>'}</tbody></table>
      <details><summary>Táblák</summary><table>${tbl}</table></details></div>
    <div class="card"><h2>Gráf (neo4j)</h2>${g}</div></div>
    <div class="card"><h2>Saját lekérdezés (csak olvasás)</h2>
      <div class="controls"><div class="seg" id="qk"><button data-v="cypher" class="on">neo4j (Cypher)</button><button data-v="sql">SQLite (SQL)</button></div>
      <select id="qp"><option value="">előre megírt…</option>${Object.keys(d.cypher).map((k) => `<option>${esc(k)}</option>`).join("")}</select></div>
      <textarea id="qt" class="ed" style="min-height:90px;margin-top:8px">MATCH (m:MunkaKor)-[:TARTALMAZ_TESZT]->(t) RETURN m.label_hu AS munkakor, count(t) AS tesz ORDER BY tesz DESC</textarea>
      <div style="margin:8px 0"><button class="primary" id="qr">Futtatás</button></div><div id="qo"></div></div>`;
  let kind = "cypher";
  $("#qk").onclick = (e) => {
    const b = e.target.closest("button"); if (!b) return; kind = b.dataset.v;
    $$("#qk button").forEach((x) => x.classList.toggle("on", x === b));
    $("#qt").value = kind === "sql" ? "SELECT status, count(*) AS db FROM jobs GROUP BY status" : $("#qt").value;
  };
  $("#qp").onchange = (e) => { if (e.target.value) { $("#qt").value = d.cypher[e.target.value]; kind = "cypher"; $$("#qk button").forEach((x) => x.classList.toggle("on", x.dataset.v === "cypher")); } };
  $("#qr").onclick = async () => {
    const r = await act(() => api("db/" + kind, { method: "POST", body: { query: $("#qt").value } }));
    $("#qo").innerHTML = table(r.rows);
  };
};
function table(rows) {
  if (!rows || !rows.length) return '<p class="muted">Nincs eredmény.</p>';
  const cols = Object.keys(rows[0]);
  const v = (x) => (x !== null && typeof x === "object" ? JSON.stringify(x) : x);
  return `<div style="overflow:auto"><table><thead><tr>${cols.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead><tbody>
    ${rows.map((r) => `<tr>${cols.map((c) => `<td class="small">${esc(v(r[c]))}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
}

// ------------------------------------------------------------------ MODELLEK
const EFFORTS = [["", "alap"], ["low", "alacsony"], ["medium", "közepes"], ["high", "magas"], ["xhigh", "nagyon magas"], ["max", "max"]];
const PRESETS = {
  "Olcsó felhő": { E0: ["anthropic", "claude-sonnet-5-5", "low", ""], P1: ["anthropic", "claude-sonnet-5-5", "low", ""],
    P2: ["anthropic", "claude-haiku-5-5", "low", ""], P3: ["anthropic", "claude-haiku-5-5", "low", "off"],
    P4: ["anthropic", "claude-sonnet-5-5", "medium", ""], P5: ["anthropic", "claude-haiku-5-5", "low", "off"] },

};
let M = null;
pages.modellek = async (view) => {
  M = await api("models");
  drawModels(view);
};
function priceOf(model) { const p = M.prices.find((x) => x.model === model); return p ? `$${p.input} / $${p.output}` : ""; }
function drawModels(view) {
  const prov = { anthropic: "Anthropic (felhő)", gemini: "Google Gemini", openai: "OpenAI-kompatibilis API", manual: "kézi (te válaszolsz)" };
  const rows = M.steps.map((s, i) => `<tr data-i="${i}">
    <td><b>${s.step}</b><div class="muted small">${esc(s.name)}</div></td>
    <td><select data-f="provider">${s.provider === "local" ? '<option value="local" selected disabled>Régi helyi beállítás – válassz szolgáltatót</option>' : ""}${M.providers.map((p) => `<option value="${p}" ${p === s.provider ? "selected" : ""}>${prov[p] || p}</option>`).join("")}</select></td>
    <td><select data-f="choice" aria-label="Elérhető modellek"></select><input data-f="model" aria-label="Egyedi modellazonosító" value="${esc(s.model)}" placeholder="Egyedi modellazonosító" style="width:200px"></td>
    <td><select data-f="effort" ${s.provider !== "anthropic" ? "disabled" : ""}>${EFFORTS.map(([v, l]) => `<option value="${v}" ${v === s.effort ? "selected" : ""}>${l}</option>`).join("")}</select></td>
    <td style="text-align:center"><input type="checkbox" data-f="thinking" ${s.thinking === "off" ? "checked" : ""}></td>
    <td><input data-f="max_tokens" type="number" step="256" value="${s.max_tokens}" style="width:90px"></td>
    <td class="small muted">${s.provider === "local" ? "ingyenes" : esc(priceOf(s.model)) || "–"}</td>
    <td class="small">${s.from_ui ? pill("done", "itt beállítva") : pill("wait", ".env / alap")}</td></tr>`).join("");
  view.innerHTML = `<h1>Modellek</h1>
    <p class="sub">Lépésenként melyik modell dolgozik. A mentés a <span class="mono">config/models.yaml</span> fájlba kerül, és erősebb a .env-nél. Az API-kulcsok a .env-ben maradnak.</p>
    <div class="card"><div class="controls" style="margin-bottom:12px"><h2 style="margin:0 auto 0 0">Lépések</h2>
      <span class="muted small">Gyors beállítás:</span>${Object.keys(PRESETS).map((k) => `<button data-p="${esc(k)}">${esc(k)}</button>`).join("")}</div>
      <table id="mt"><thead><tr><th>Lépés</th><th>Szolgáltató</th><th>Modell</th><th>Gondolkodás</th><th title="Gondolkodás kikapcsolása (Sonnet 5.5, Haiku 5.5, Qwen)">Gondolkodás ki</th><th>Max. kimenet</th><th>Ár / 1M token (be / ki)</th><th>Forrás</th></tr></thead><tbody>${rows}</tbody></table>
      <div class="controls">${["anthropic", "gemini", "openai"].map(p => `<button data-discover="${p}">${esc(prov[p])}: modelllista frissítése</button>`).join("")}</div><p id="catalog-result" class="small muted">A lista a szolgáltató API-jából frissíthető. Egyedi modellazonosító is megadható.</p>
      <p class="muted small">P3 = egyesítés, P4 = KÉSZ-ség és EQF (kézi parancs), P5 = tanúsítási szöveg.</p></div>
    <div class="row">
      <div class="card"><h2>Bírálat (P2)</h2>
        <div class="seg" id="p2m">${[["auto", "csak ha kell"], ["always", "mindig"], ["never", "soha"]].map(([v, l]) => `<button data-v="${v}" class="${M.p2.mode === v ? "on" : ""}">${l}</button>`).join("")}</div>
        <p class="muted small">„Csak ha kell”: ha a szabályellenőrzés jelez, vagy a munkakör a véletlen mintába esik.</p>
        <label class="f">Véletlen minta: <b id="p2v">${Math.round(M.p2.sample * 100)}%</b><input id="p2s" type="range" min="0" max="1" step="0.05" value="${M.p2.sample}" style="width:220px"></label></div>
    </div>
    <div class="card"><h2>OpenAI-kompatibilis API</h2>
      <label>Cím <input id="ob" value="${esc(M.openai.base_url)}"></label>
      <label>API-kulcs környezeti változója <input id="okey" value="${esc(M.openai.api_key_env)}"></label>
      <label>JSON-kényszer <select id="oj">${["object", "schema", "none"].map(v => `<option ${v === M.openai.json_mode ? "selected" : ""}>${v}</option>`).join("")}</select></label>
      <label>Válasz időkorlátja (mp) <input id="otimeout" type="number" min="1" max="7200" value="${M.openai.timeout}"></label>
      <p class="small muted">A kulcsot a .env fájlba írd; itt csak a változó nevét add meg. A cím tartalmazza a /v1 útvonalat. A kapcsolati próba a most megadott címet és kulcsváltozót használja, mentés nélkül. A próba nem indít szöveggenerálást.</p>
      <button id="otest">Kapcsolati próba</button><p id="ores" role="status" aria-live="polite" class="small"></p></div>
    <div class="card"><h2>Helyi beágyazás (embed)</h2>
      <label>Szolgáltató <select id="ep">${["ollama", "sentence-transformers"].map(v => `<option ${v === M.embed.provider ? "selected" : ""}>${v}</option>`).join("")}</select></label>
      <label>Modell <input id="em" value="${esc(M.embed.model)}"></label>
      <label>Ollama címe <input id="eh" value="${esc(M.embed.host)}"></label>
      <label>Szövegelőtag <input id="ex" value="${esc(M.embed.prefix)}"></label>
      <button id="etest">Beágyazás ellenőrzése</button><p id="eres" class="small"></p>
      <p class="small muted">Üres modell: kikapcsolva. A próba valódi vektort kér; sentence-transformers esetén modellletöltést indíthat. Modellcsere után a meglévő gráf vektorait és indexeit is frissíteni kell.</p></div>
    <div class="card"><h2>Egységárak (USD / 1M token)</h2><p class="small muted">Ellenőrizd a szolgáltatói díjakat. Az ismeretlen ár külön jelzést kap.</p>
      <p class="small muted">${esc(M.gemini_price_note)} <a href="https://ai.google.dev/gemini-api/docs/pricing" target="_blank" rel="noopener noreferrer">Hivatalos Gemini-árak</a></p><table id="prices"><thead><tr><th>Modell</th><th>Bemenet</th><th>Kimenet</th><th>Cache olvasás</th></tr></thead><tbody>${[...M.prices, {model:"", input:0, output:0, cache:0}].map(r => `<tr>${["model", "input", "output", "cache"].map(k => `<td><input data-price="${k}" type="${k === "model" ? "text" : "number"}" ${k === "model" ? "" : 'min="0" step="any"'} value="${esc(r[k])}"></td>`).join("")}</tr>`).join("")}</tbody></table></div>
    <div class="controls"><button class="primary" id="msave">Mentés</button>${M.has_ui_file ? '<button id="mreset">Felületi beállítás törlése (vissza a .env-re)</button>' : ""}</div>`;
  const p2 = { mode: M.p2.mode };
  $("#p2m").onclick = (e) => { const b = e.target.closest("button"); if (!b) return; p2.mode = b.dataset.v; $$("#p2m button").forEach((x) => x.classList.toggle("on", x === b)); };
  $("#p2s").oninput = (e) => ($("#p2v").textContent = Math.round(e.target.value * 100) + "%");
  const updateChoices = (tr, reset = false) => {
    const provider = $("[data-f=provider]", tr).value;
    const input = $("[data-f=model]", tr), select = $("[data-f=choice]", tr);
    const ids = [...new Set([...(M.catalog[provider] || []), ...M.steps.filter(s => s.provider === provider).map(s => s.model)].filter(Boolean))];
    if (reset) { input.value = ids[0] || ""; $("[data-f=effort]", tr).value = ""; $("[data-f=thinking]", tr).checked = false; }
    select.innerHTML = `<option value="">Egyedi modellazonosító…</option>` + ids.map(m => `<option value="${esc(m)}">${esc(m)}${m.startsWith("gemini-2.5-") ? " (régi modell, új felhasználóknak nem elérhető)" : ""}${priceOf(m) ? " · " + esc(priceOf(m)) + " / 1M token" : " · ár nincs megadva"}</option>`).join("");
    select.value = ids.includes(input.value) ? input.value : "";
    select.onchange = () => { input.value = select.value; input.focus(); };
    input.oninput = () => { select.value = ids.includes(input.value) ? input.value : ""; };
    const priceCell = tr.children[6];
    const updatePrice = () => { priceCell.textContent = provider === "local" ? "ingyenes" : priceOf(input.value) || "ár nincs megadva"; };
    const choiceChanged = select.onchange, typed = input.oninput;
    select.onchange = () => { choiceChanged(); updatePrice(); };
    input.oninput = () => { typed(); updatePrice(); };
    updatePrice();
    $("[data-f=effort]", tr).disabled = provider !== "anthropic";
  };
  $$("#mt tbody tr").forEach(tr => { updateChoices(tr); $("[data-f=provider]", tr).onchange = () => updateChoices(tr, true); });
  $$('[data-discover]').forEach(button => button.onclick = async () => {
    button.disabled = true;
    try {
      const provider = button.dataset.discover;
      const r = await api("models/discover", {method:"POST", body:{provider,
        base_url: $("#ob").value, api_key_env:$("#okey").value}});
      $("#catalog-result").textContent = r.ok ? `${r.models.length} elérhető modell (${provider})` : r.error;
      if (r.ok) { M.catalog[provider] = r.models; $$("#mt tbody tr").forEach(tr => updateChoices(tr)); }
    } catch(e) { toast(e.message, true); } finally { button.disabled = false; }
  });
  const embedValues = () => ({provider:$("#ep").value, model:$("#em").value.trim(), host:$("#eh").value.trim(), prefix:$("#ex").value});
  $("#etest").onclick = async () => {
    $("#etest").disabled = true; $("#eres").textContent = "Ellenőrzés…";
    try { const r = await api("models/embed-test", {method:"POST", body:embedValues()});
      $("#eres").textContent = r.ok ? `Elérhető. Dimenzió: ${r.dimension}; magyar–angol: ${r.similarity.toFixed(3)}; eltérő fogalom: ${r.unrelated.toFixed(3)}` : r.error;
    } catch(e) { toast(e.message, true); } finally { $("#etest").disabled = false; }
  };
  $$("[data-p]").forEach((b) => b.onclick = () => {
    const P = PRESETS[b.dataset.p];
    $$("#mt tbody tr").forEach((tr) => {
      const [pr, mo, ef, th] = P[M.steps[tr.dataset.i].step];
      $("[data-f=provider]", tr).value = pr; $("[data-f=model]", tr).value = mo; $("[data-f=effort]", tr).value = ef;
      $("[data-f=effort]", tr).disabled = pr !== "anthropic"; $("[data-f=thinking]", tr).checked = th === "off";
    });
    $$("#mt tbody tr").forEach(tr => updateChoices(tr));
    toast(`„${b.dataset.p}” betöltve; a Mentéssel lép életbe.`);
  });
  $("#otest").onclick = async () => {
    const button = $("#otest"), result = $("#ores");
    button.disabled = true; result.textContent = "Kapcsolat ellenőrzése…";
    try {
      const r = await api("models/openai-test", {method:"POST", body:{
        base_url:$("#ob").value.trim(), api_key_env:$("#okey").value.trim()}});
      result.textContent = r.ok ? `Kapcsolat rendben. Modelllista lekérve: ${r.models.length} modell. A generálási jogosultság külön ellenőrzést igényel.` : `Kapcsolati hiba: ${r.error}`;
      if (r.ok) { M.catalog.openai = r.models; $$("#mt tbody tr").forEach(tr => updateChoices(tr)); }
    } catch(e) { result.textContent = `Kapcsolati hiba: ${e.message}`; }
    finally { button.disabled = false; }
  };
  $("#msave").onclick = async () => {
    const steps = $$("#mt tbody tr").map((tr) => ({ step: M.steps[tr.dataset.i].step, provider: $("[data-f=provider]", tr).value,
      model: $("[data-f=model]", tr).value.trim(), effort: $("[data-f=effort]", tr).disabled ? "" : $("[data-f=effort]", tr).value,
      thinking: $("[data-f=thinking]", tr).checked ? "off" : "", max_tokens: +$("[data-f=max_tokens]", tr).value }));
    M = await act(() => api("models", { method: "POST", body: { steps, embed:embedValues(),
      openai:{base_url:$("#ob").value.trim(), api_key_env:$("#okey").value.trim(), json_mode:$("#oj").value, timeout:+$("#otimeout").value},
      prices:$$("#prices tbody tr").map(tr => Object.fromEntries($$("input", tr).map(input => [input.dataset.price, input.dataset.price === "model" ? input.value.trim() : +input.value]))), p2: { mode: p2.mode, sample: +$("#p2s").value } } }), "Mentve. A következő futtatás már ezzel indul.");
    drawModels(view);
  };
  if ($("#mreset")) $("#mreset").onclick = async () => { M = await act(() => api("models", { method: "DELETE" }), "Törölve, a .env érvényes."); drawModels(view); };
}

// ------------------------------------------------------------------ PROMPTOK
let P = { name: null, saved: "" };
pages.promptok = async (view) => {
  const d = await api("prompts");
  view.innerHTML = `<h1>Promptok</h1><p class="sub">Jelenlegi promptverzió: <span class="mono">${esc(d.prompt_version)}</span>. Minden mentés új verziót ad; a már kész munkakörök nem futnak újra maguktól.</p>
    <div class="prompts"><div class="card plist">${d.prompts.map((p) => `<div data-n="${esc(p.name)}"><b>${esc(p.name)}</b> <span class="tag">${esc(p.step)}</span>
      <div class="muted small">${esc(p.about)}</div><div class="muted small">${esc(p.modified)}${p.versions ? ` · ${p.versions} korábbi változat` : ""}</div></div>`).join("")}</div>
    <div class="card" id="pe"><p class="muted">Válassz egy promptot a listából.</p></div></div>`;
  $$(".plist [data-n]").forEach((el) => el.onclick = () => openPrompt(el.dataset.n));
  openPrompt(P.name || d.prompts[0].name);
};
async function openPrompt(name) {
  const ed = $("#pe .ed");
  if (ed && P.name && ed.value !== P.saved && !confirmLeave()) return;
  const d = await api("prompts/" + name);
  P = { name, saved: d.text };
  $$(".plist [data-n]").forEach((el) => el.classList.toggle("on", el.dataset.n === name));
  $("#pe").innerHTML = `<div class="controls" style="margin-bottom:8px"><h2 style="margin:0 auto 0 0">${esc(name)} <span class="tag">${esc(d.step)}</span></h2>
      <span id="pst" class="muted small">mentve</span><button id="pdiff" disabled>Eltérés</button><button id="pundo" disabled>Elvetés</button><button class="primary" id="psave" disabled>Mentés</button></div>
    <p class="muted small" style="margin-top:0">${esc(d.about)} · <a href="#modellek">A(z) ${esc(d.step)} lépés modelljének kiválasztása</a></p>
    <textarea class="ed" spellcheck="false">${esc(d.text)}</textarea><div id="pdo"></div>
    ${d.history.length ? `<details style="margin-top:12px"><summary>Korábbi változatok (${d.history.length})</summary>
      <div class="controls"><select id="ph">${d.history.map((h) => `<option>${esc(h)}</option>`).join("")}</select><button id="phd">Eltérés a jelenlegitől</button><button id="phr">Betöltés a szerkesztőbe</button></div><div id="pho"></div></details>` : ""}`;
  const ta = $("#pe .ed");
  const dirty = () => { const x = ta.value !== P.saved; $("#psave").disabled = $("#pundo").disabled = $("#pdiff").disabled = !x; $("#pst").textContent = x ? "módosítva, nincs mentve" : "mentve"; };
  ta.oninput = dirty;
  $("#pundo").onclick = () => { ta.value = P.saved; dirty(); $("#pdo").innerHTML = ""; };
  $("#pdiff").onclick = async () => showDiff("#pdo", P.saved, ta.value);
  $("#psave").onclick = async () => {
    const r = await act(() => api("prompts/" + name, { method: "POST", body: { text: ta.value } }), (r) => `Mentve. Új promptverzió: ${r.prompt_version}`);
    if (r.saved) { P.saved = ta.value; dirty(); $("#pdo").innerHTML = ""; pages.promptok($("#view")); }
  };
  if ($("#phd")) {
    $("#phd").onclick = async () => { const o = await api(`prompts/${name}/history/${encodeURIComponent($("#ph").value)}`); showDiff("#pho", o.text, P.saved); };
    $("#phr").onclick = async () => { const o = await api(`prompts/${name}/history/${encodeURIComponent($("#ph").value)}`); ta.value = o.text; dirty(); toast("Betöltve; a Mentéssel állítod vissza."); };
  }
}
function confirmLeave() { return window.confirm("A módosítás nincs mentve. Elveted?"); }
async function showDiff(sel, a, b) {
  const { diff } = await api("prompts/x/diff", { method: "POST", body: { a, b } });
  $(sel).innerHTML = `<pre class="diff" style="margin-top:8px">${diff ? diff.split("\n").map((l) =>
    `<span class="${l.startsWith("+") ? "add" : l.startsWith("-") ? "del" : ""}">${esc(l)}</span>`).join("\n") : "nincs eltérés"}</pre>`;
}

// ------------------------------------------------------------------ FELTÖLTÉS
pages.feltoltes = async (view) => {
  view.innerHTML = `<h1>Feltöltés</h1><p class="sub">Dokumentumokból a kinyerés (E0) készít munkakör-rekordot; kész rekordfájlt közvetlenül importálhatsz. Az új és a megváltozott rekord a Futás oldalon várakozóként jelenik meg.</p>
    <div class="row"><div class="card"><h2>Dokumentumok <span class="muted small">pdf, docx, txt, md, html, odt, rtf</span></h2>
      <div class="drop" data-k="doc">Húzd ide a fájlokat, vagy <label style="color:var(--accent);cursor:pointer">válaszd ki<input type="file" multiple hidden></label></div><div id="docs"></div></div>
    <div class="card"><h2>Munkakör-rekordok <span class="muted small">jsonl, json, csv, xlsx</span></h2>
      <div class="drop" data-k="rec">Húzd ide a fájlt, vagy <label style="color:var(--accent);cursor:pointer">válaszd ki<input type="file" multiple hidden></label></div><div id="recs"></div><div id="prev"></div></div></div>`;
  $$(".drop").forEach((dz) => {
    const up = async (files) => {
      for (const f of files) await act(() => api(`files/${dz.dataset.k}/${encodeURIComponent(f.name)}`, { method: "PUT", body: f, raw: true }), `Feltöltve: ${f.name}`);
      drawFiles();
    };
    dz.ondragover = (e) => { e.preventDefault(); dz.classList.add("over"); };
    dz.ondragleave = () => dz.classList.remove("over");
    dz.ondrop = (e) => { e.preventDefault(); dz.classList.remove("over"); up([...e.dataTransfer.files]); };
    $("input", dz).onchange = (e) => up([...e.target.files]);
  });
  drawFiles();
};
async function drawFiles() {
  const d = await api("files");
  $("#docs").innerHTML = d.docs.length ? `<table><tbody>${d.docs.map((f) => `<tr><td><input type="checkbox" class="dk" value="${esc(f.name)}"></td><td>${esc(f.name)}</td><td class="muted small">${f.kb} kB · ${esc(f.modified)}</td><td><button class="danger small" data-del="${esc(f.name)}">Törlés</button></td></tr>`).join("")}</tbody></table>
    <div class="controls" style="margin-top:10px"><button class="primary" id="ex">Kinyerés (E0) és importálás</button></div>
    <p class="muted small">A kinyerés a Futás oldalon követhető; a végén a munkakörök várakozóként jelennek meg.</p>` : '<p class="muted">Még nincs feltöltött dokumentum.</p>';
  $$("#docs [data-del]").forEach((b) => b.onclick = () => act(() => api(`files/doc/${encodeURIComponent(b.dataset.del)}`, { method: "DELETE" }), "Törölve.").then(drawFiles));
  if ($("#ex")) $("#ex").onclick = async () => {
    const files = $$(".dk:checked").map((x) => x.value);
    const r = await act(() => api("extract", { method: "POST", body: { files } }), "A kinyerés elindult.");
    ui.task = r.task; location.hash = "#futas";
  };
  $("#recs").innerHTML = d.records.length ? `<table><tbody>${d.records.map((f) => `<tr class="click" data-f="${esc(f.name)}"><td>${esc(f.name)}</td><td class="muted small">${f.kb} kB · ${esc(f.modified)}</td><td><button class="danger small" data-del="${esc(f.name)}">Törlés</button></td></tr>`).join("")}</tbody></table>
    <p class="muted small">Kattints egy fájlra az előnézethez és az importáláshoz.</p>` : '<p class="muted">A data mappában nincs rekordfájl.</p>';
  $$("#recs [data-del]").forEach((b) => b.onclick = (e) => { e.stopPropagation(); act(() => api(`files/rec/${encodeURIComponent(b.dataset.del)}`, { method: "DELETE" }), "Törölve.").then(drawFiles); });
  $$("#recs tr[data-f]").forEach((tr) => tr.onclick = async () => {
    $$("#recs tr").forEach((x) => x.classList.toggle("sel", x === tr));
    const name = tr.dataset.f;
    $("#prev").innerHTML = "betöltés…";
    let p;
    try { p = await api(`files/rec/${encodeURIComponent(name)}`); } catch (e) { $("#prev").innerHTML = `<p class="bad">${esc(e.message)}</p>`; return; }
    $("#prev").innerHTML = `<h2 style="margin-top:14px">${esc(name)}</h2><div class="kpis"><div><div class="big">${fmt(p.count)}</div><div class="muted">rekord</div></div>
      <div><div class="big ok">${fmt(p.new)}</div><div class="muted">új</div></div><div><div class="big">${fmt(p.changed)}</div><div class="muted">megváltozott</div></div>
      <div><div class="big" style="color:var(--muted)">${fmt(p.same)}</div><div class="muted">változatlan (nem fut újra)</div></div></div>
      ${table(p.sample.map((r) => ({ azonosító: r.job_id, cím: r.title, feladatok: r.tasks })))}
      <div class="controls" style="margin-top:10px"><button class="primary" id="imp" ${p.new + p.changed ? "" : "disabled"}>Importálás (${p.new + p.changed} rekord)</button></div>`;
    $("#imp").onclick = () => act(() => api("import", { method: "POST", body: { file: name } }), (r) => `${r.count} rekord beolvasva, ${r.new} új vagy változott.`).then(() => tr.click());
  });
}

route();
refreshLive();
