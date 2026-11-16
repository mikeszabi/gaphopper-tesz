'use strict';
/* Gaphopper kompetencialabor
   1. Munkakör-profil (lista / dokumentum / szöveg → felbontott munkakör, kompetenciák TESZ-nivalókon át, EQF)
   2. Egyéni profil (CV / szöveg / kézi → kompetenciák EQF-becsléssel)
   3. Munkakör-ajánló (2. alapján, minden felbontott munkakör rangsorolva, hiányokkal)
   4. Illeszkedés és hiány (2. × kiválasztott munkakörök, legfeljebb 6)
   A kiválasztott munkakörök és az egyéni profil a felső kontextussávban látszik, és a böngészőben megmarad. */

const $ = (id) => document.getElementById(id);
const MAX_JOBS = 6;
const TABS = ['job', 'person', 'recommend', 'gap', 'space'];
const STORE_KEY = 'gh-lab-v1';
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const pct = (x) => Math.round((x || 0) * 100) + '%';
const STATUS = { ok: ['✓', 'megvan'], under: ['▽', 'szint alatt'], missing: ['✕', 'hiányzik'] };
const BAND = { kesz: 'kész illeszkedés', kozeli: 'közeli', fejlesztendo: 'fejlesztendő' };
const REL = { CORE: 'alap', SUPPORTING: 'támogató' };
const FREQ = { DAILY: 'naponta', WEEKLY: 'hetente', MONTHLY: 'havonta', RARE: 'ritkán' };

const state = {
  tab: 'job', jobs: [], active: null, profiles: {}, catalog: {}, person: null,
  gap: null, rec: null, gapActive: null, showAll: {}, space: null, spaceFocus: null, spaceView: null,
};

// ------------------------------------------------------------------ tárolás (csak ebben a böngészőben)
function save() {
  try { localStorage.setItem(STORE_KEY, JSON.stringify({ jobs: state.jobs, active: state.active, person: state.person })); } catch { /* privát mód */ }
}
function restore() {
  try {
    const d = JSON.parse(localStorage.getItem(STORE_KEY) || '{}');
    state.jobs = Array.isArray(d.jobs) ? d.jobs.slice(0, MAX_JOBS) : [];
    state.active = d.active || state.jobs[0]?.id || null;
    state.person = normPerson(d.person);
  } catch { /* üres kezdés */ }
}

// ------------------------------------------------------------------ apró segédek
function setStatus(id, text, err = false) { const e = $(id); e.textContent = text || ''; e.classList.toggle('err', !!err); }
function eqfBadge(n, label = 'EQF') { return n ? `<span class="eqf">${label} ${n}</span>` : `<span class="eqf none">${label} –</span>`; }
function bar(x, cls = '') { return `<div class="bar ${cls}"><i style="width:${Math.max(0, Math.min(1, x)) * 100}%"></i></div>`; }
function stPill(status, level) { const [g, t] = STATUS[status]; return `<span class="st ${status}" data-tip="${esc(t)}">${g}${level != null ? ' ' + level : ''}</span>`; }
function groupBy(items, key) { const m = new Map(); for (const x of items) { const k = x[key] || 'Egyéb'; if (!m.has(k)) m.set(k, []); m.get(k).push(x); } return m; }
function compLabel(id) { return state.catalog[id]?.label || id; }

// tooltip: minden [data-tip] elem (egér és billentyűzetfókusz)
const tip = $('tip');
function showTip(el) {
  const t = el.getAttribute('data-tip'); if (!t) return;
  tip.innerHTML = t; tip.hidden = false;
  const r = el.getBoundingClientRect(), w = tip.offsetWidth, h = tip.offsetHeight;
  let x = r.left + r.width / 2 - w / 2, y = r.top - h - 8;
  if (y < 4) y = r.bottom + 8;
  tip.style.left = Math.max(4, Math.min(innerWidth - w - 4, x)) + 'px'; tip.style.top = y + 'px';
}
document.addEventListener('mouseover', (e) => { const el = e.target.closest('[data-tip]'); if (el) showTip(el); else tip.hidden = true; });
document.addEventListener('focusin', (e) => { const el = e.target.closest('[data-tip]'); if (el) showTip(el); });
document.addEventListener('scroll', () => { tip.hidden = true; }, true);

// ------------------------------------------------------------------ EQF-skála (1–8), SVG
/* opts: req (elvárt szint), own (egyéni szint, null = nincs meg), status; w szélesség */
function eqfScale({ req = null, own, status = null, w = 168, showOwn = false, ticks = true }) {
  const pad = 8, x = (n) => pad + ((n - 1) / 7) * (w - 2 * pad), y = 11, h = ticks ? 26 : 22;
  let s = `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true">`;
  s += `<line class="axis" x1="${pad}" x2="${w - pad}" y1="${y}" y2="${y}"/>`;
  for (let n = 1; n <= 8; n++) {
    s += `<line class="grid" x1="${x(n)}" x2="${x(n)}" y1="${y - 3}" y2="${y + 3}"/>`;
    if (ticks) s += `<text x="${x(n)}" y="${h - 1}" text-anchor="middle">${n}</text>`;
  }
  const hasOwn = own !== undefined && own !== null;
  if (req && hasOwn && own < req) s += `<line class="link-${status || 'under'}" x1="${x(own)}" x2="${x(req)}" y1="${y}" y2="${y}"/>`;
  if (req && !hasOwn && showOwn) s += `<line class="link-missing" x1="${pad}" x2="${x(req)}" y1="${y}" y2="${y}" opacity=".5"/>`;
  if (req) s += `<circle class="req" cx="${x(req)}" cy="${y}" r="5.5"/>`;
  if (hasOwn) s += `<circle class="own" cx="${x(own)}" cy="${y}" r="${req ? 4.5 : 5.5}"/>`;
  else if (showOwn) s += `<circle class="own none" cx="${pad}" cy="${y}" r="4"/>`;
  return s + '</svg>';
}

// ------------------------------------------------------------------ fülek és forrásválasztók
function setTab(tab) {
  state.tab = tab;
  document.querySelectorAll('.tabs button').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.tab === tab)));
  for (const t of TABS) $('pane-' + t).hidden = t !== tab;
  if (tab === 'recommend') runRecommend();
  if (tab === 'gap') runGap();
  if (tab === 'space') runSpace();
  try { history.replaceState(null, '', '#' + tab); } catch { /* */ }
}
document.querySelectorAll('.tabs button').forEach((b) => (b.onclick = () => setTab(b.dataset.tab)));
document.querySelectorAll('.panel .seg').forEach((seg) => {
  const panel = seg.closest('.panel');
  seg.querySelectorAll('button').forEach((b) => (b.onclick = () => {
    seg.querySelectorAll('button').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
    panel.querySelectorAll('[data-src-pane]').forEach((p) => (p.hidden = p.dataset.srcPane !== b.dataset.src));
  }));
});

// ------------------------------------------------------------------ kontextussáv
function renderContext() {
  $('ctx-jobs-count').textContent = `${state.jobs.length} / ${MAX_JOBS}`;
  $('ctx-jobs').innerHTML = state.jobs.length
    ? state.jobs.map((j) => `<span class="chip pick ${j.id === state.active ? 'active' : ''}" data-job="${esc(j.id)}" tabindex="0" data-tip="${esc(j.title)} · ${esc(j.id)}"><span class="t">${esc(j.title)}</span>${eqfBadge(j.eqf)}<button type="button" data-remove="${esc(j.id)}" aria-label="${esc(j.title)} eltávolítása">×</button></span>`).join('')
    : '<span class="muted">Még nincs kiválasztott munkakör — az 1. fülön adhatsz hozzá.</span>';
  $('ctx-jobs').querySelectorAll('[data-remove]').forEach((b) => (b.onclick = (e) => { e.stopPropagation(); removeJob(b.dataset.remove); }));
  $('ctx-jobs').querySelectorAll('[data-job]').forEach((c) => (c.onclick = c.onkeydown = (e) => {
    if (e.type === 'keydown' && e.key !== 'Enter') return;
    state.active = c.dataset.job; state.gapActive = c.dataset.job; save(); renderContext(); renderJobStage(); if (state.gap) renderGap();
  }));
  const p = state.person;
  $('ctx-person-clear').hidden = !p;
  $('ctx-person').innerHTML = p && p.competencies.length
    ? `<span class="chip"><span class="t">${esc(p.source || 'Egyéni profil')}</span><span class="muted small">${activeComps().length} kompetencia${p.off?.length ? ' (szűrve)' : ''}</span>${eqfBadge(p.eqf)}</span>`
    : '<span class="muted">Még nincs egyéni profil — a 2. fülön készítheted el.</span>';
}
$('ctx-person-clear').onclick = () => { if (confirm('Törlöd az egyéni profilt?')) { state.person = null; changed('person'); } };

function changed(what) {
  save(); renderContext();
  if (what === 'jobs') { renderJobList(); renderJobStage(); }
  if (what === 'person') { renderPerson(); renderSkillList(); }
  state.gap = null; state.rec = what === 'jobs' ? state.rec : null;
  state.space = null;
  if (state.tab === 'gap') runGap();
  if (state.tab === 'recommend' && what === 'person') runRecommend();
  if (state.tab === 'space') runSpace();
}

function addJob(job) {
  if (state.jobs.some((j) => j.id === job.id)) { state.active = job.id; changed('jobs'); return true; }
  if (state.jobs.length >= MAX_JOBS) { alert(`Legfeljebb ${MAX_JOBS} munkakör vethető össze egyszerre. Előbb távolíts el egyet a felső sávból.`); return false; }
  state.jobs.push({ id: job.id, title: job.title, eqf: job.eqf ?? null });
  state.active = job.id; changed('jobs'); return true;
}
function removeJob(id) {
  state.jobs = state.jobs.filter((j) => j.id !== id);
  if (state.active === id) state.active = state.jobs[0]?.id || null;
  if (state.gapActive === id) state.gapActive = null;
  changed('jobs');
}

// ------------------------------------------------------------------ 1. munkakör-profil
let jobRows = [], jobTimer;
async function loadJobs() {
  const d = await api('lab/jobs?q=' + encodeURIComponent($('job-search').value.trim()));
  jobRows = d.items; renderJobList();
  $('job-total').textContent = `${d.total} felbontott munkakör${$('job-search').value.trim() ? ' a szűrésre' : ''}`;
}
$('job-search').oninput = () => { clearTimeout(jobTimer); jobTimer = setTimeout(() => loadJobs().catch((e) => setStatus('job-status', e.message, true)), 250); };
function renderJobList() {
  const sel = new Set(state.jobs.map((j) => j.id));
  $('job-list').innerHTML = jobRows.slice(0, 300).map((j) => `<li class="${sel.has(j.id) ? 'on' : ''}"><span class="t">${esc(j.title)}<small>${j.competencies} kompetencia · ${esc(j.id)}</small></span>${eqfBadge(j.eqf)}<button type="button" class="icon" data-add="${esc(j.id)}" ${sel.has(j.id) ? 'disabled' : ''} aria-label="${esc(j.title)} hozzáadása">${sel.has(j.id) ? '✓' : '+'}</button></li>`).join('');
  $('job-list').querySelectorAll('[data-add]').forEach((b) => (b.onclick = () => addJob(jobRows.find((j) => j.id === b.dataset.add))));
}

async function documentBody(fileId, textId) {
  const file = $(fileId)?.files?.[0], text = $(textId)?.value?.trim();
  if (file) { if (file.size > 8 * 1024 * 1024) throw new Error('A fájl legfeljebb 8 MB lehet.'); const f = new FormData(); f.append('file', file); return f; }
  if (!text || text.length < 20) throw new Error('Adj meg legalább 20 karakteres szöveget, vagy válassz fájlt.');
  return { text };
}
document.querySelectorAll('[data-identify]').forEach((b) => (b.onclick = async () => {
  const src = b.dataset.identify;
  b.disabled = true; $('job-candidates').innerHTML = '';
  try {
    const body = await documentBody(src === 'file' ? 'job-file' : null, src === 'text' ? 'job-text' : null);
    setStatus('job-status', 'Azonosítás az AI-val… (fél–két perc)');
    const d = await api('lab/identify-job', body);
    setStatus('job-status', d.matches.length ? 'Válaszd ki, melyik munkakört veszed fel.' : 'Nem találtunk megfelelő felbontott munkakört.');
    renderCandidates(d);
  } catch (e) { setStatus('job-status', e.message, true); } finally { b.disabled = false; }
}));
function renderCandidates(d) {
  const sel = new Set(state.jobs.map((j) => j.id));
  $('job-candidates').innerHTML = d.matches.map((m) => `<div class="cand"><h3><span>${esc(m.label)}</span><span class="conf" data-tip="Az AI saját bizonyossági becslése, nem kalibrált valószínűség.">${pct(m.confidence)} biztos</span></h3>
      <p class="small">${esc(m.reason)}</p>${m.evidence.map((q) => `<blockquote>„${esc(q)}”</blockquote>`).join('')}
      <button type="button" class="secondary" data-cand="${esc(m.id)}" ${sel.has(m.id) ? 'disabled' : ''}>${sel.has(m.id) ? 'Már a kiválasztásban' : '+ Hozzáadás a kiválasztáshoz'}</button></div>`).join('')
    + (d.extracted_tasks.length ? `<details class="io"><summary>A szövegből kinyert feladatok (${d.extracted_tasks.length})</summary><ul class="small">${d.extracted_tasks.map((t) => `<li>${esc(t)}</li>`).join('')}</ul></details>` : '')
    + d.warnings.map((w) => `<p class="warning">${esc(w)}</p>`).join('');
  $('job-candidates').querySelectorAll('[data-cand]').forEach((b) => (b.onclick = async () => {
    const m = d.matches.find((x) => x.id === b.dataset.cand);
    const row = jobRows.find((j) => j.id === m.id) || (await api('lab/jobs?q=' + encodeURIComponent(m.id))).items.find((j) => j.id === m.id);
    if (addJob(row || { id: m.id, title: m.label })) { b.disabled = true; b.textContent = 'Hozzáadva'; }
  }));
}

async function ensureProfiles(ids) {
  const need = ids.filter((id) => !state.profiles[id]);
  if (need.length) {
    const d = await api('lab/job-profiles', { job_ids: need });
    for (const p of d.profiles) state.profiles[p.id] = p;
  }
  // a chipek EQF-jelzése a profilból (pl. AI-azonosításból felvett munkakörnél)
  for (const j of state.jobs) if (state.profiles[j.id] && j.eqf == null) j.eqf = state.profiles[j.id].eqf.typical;
  return ids.map((id) => state.profiles[id]).filter(Boolean);
}

async function renderJobStage() {
  const stage = $('job-stage');
  if (!state.jobs.length) {
    stage.innerHTML = `<div class="empty"><h2>Válassz munkakört</h2><p>Bal oldalt listából, álláshirdetésből vagy szabad szövegből adhatsz hozzá munkakört (legfeljebb ${MAX_JOBS}-ot).<br>A profil a munkakör TESZ-nivalóin keresztül mutatja a kompetenciákat és az elvárt EQF-szintet.</p></div>`;
    return;
  }
  let profiles;
  try { profiles = await ensureProfiles(state.jobs.map((j) => j.id)); renderContext(); }
  catch (e) { stage.innerHTML = `<div class="empty status err">${esc(e.message)}</div>`; return; }
  if (!profiles.some((p) => p.id === state.active)) state.active = profiles[0]?.id;
  const active = state.profiles[state.active];
  stage.innerHTML = (profiles.length > 1 ? compareMatrix(profiles) : '') + (active ? jobDetail(active) : '');
  wireJobStage(stage);
}

function compareMatrix(profiles) {
  // a kiválasztott munkakörök kompetenciáinak uniója, kategóriánként, a legnagyobb fontosság szerint
  const rows = new Map();
  for (const p of profiles) for (const c of p.competencies) {
    const r = rows.get(c.id) || { ...c, by: {}, top: 0 };
    r.by[p.id] = c; r.top = Math.max(r.top, c.importance); rows.set(c.id, r);
  }
  const all = [...rows.values()].sort((a, b) => b.top - a.top);
  const key = 'cmp', limit = state.showAll[key] ? all.length : 30, shown = new Set(all.slice(0, limit).map((r) => r.id));
  const grouped = groupBy(all.filter((r) => shown.has(r.id)), 'category_label');
  const head = profiles.map((p) => `<th class="job ${p.id === state.active ? 'active' : ''}" data-col="${esc(p.id)}" tabindex="0" data-tip="Kattints a részletes profilért">${esc(p.title)}<br>${eqfBadge(p.eqf.typical)}</th>`).join('');
  let body = '';
  for (const [cat, items] of grouped) {
    body += `<tr class="cat"><td colspan="${profiles.length + 1}">${esc(cat)}</td></tr>`;
    for (const r of items) {
      body += `<tr><td class="lab">${esc(r.label)}<span class="sub">${esc(r.group_label)}</span></td>` + profiles.map((p) => {
        const c = r.by[p.id];
        if (!c) return '<td class="cell"><span class="muted">·</span></td>';
        const size = 10 + Math.round(c.importance * 14);
        return `<td class="cell"><span data-tip="<b>${esc(r.label)}</b>${esc(p.title)}<br>elvárt EQF: ${c.required_eqf ?? '–'} · fontosság: ${pct(c.importance)}<br>${c.tesz.length} TESZ-SZÍN igényli" tabindex="0">${dotSvg(size, c.required_eqf, c.importance)}</span></td>`;
      }).join('') + '</tr>';
    }
  }
  return `<div class="card"><div class="card-head"><div><span class="eyebrow">ÖSSZEVETÉS</span><h2>${profiles.length} munkakör kompetenciái</h2></div>
    <div class="legend"><span><svg width="16" height="16" style="display:inline;vertical-align:-3px">${dotInner(16, null, 1)}</svg> nagyobb kör = fontosabb</span><span>szám = elvárt EQF-szint</span><span class="muted">· = nem igényli</span></div></div>
    <div class="scroll-x"><table class="matrix"><thead><tr><th>Kompetencia</th>${head}</tr></thead><tbody>${body}</tbody></table></div>
    ${all.length > 30 ? `<button type="button" class="link more" data-more="${key}">${state.showAll[key] ? 'Csak a 30 legfontosabb' : `Mind a ${all.length} kompetencia`}</button>` : ''}</div>`;
}
function dotInner(size, level, imp) {
  const r = size / 2 - 1;
  return `<circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="var(--accent)" opacity="${0.35 + 0.65 * imp}"/>` + (level ? `<text x="${size / 2}" y="${size / 2 + 3.5}" text-anchor="middle" style="fill:#fff;font-size:10px;font-weight:650">${level}</text>` : '');
}
function dotSvg(size, level, imp) { return `<svg width="${size}" height="${size}" style="margin:auto">${dotInner(size, level, imp)}</svg>`; }

function eqfDistribution(p) {
  // a TESZ-SZÍN-ek EQF-szintenkénti súlya: egysoros oszlopdiagram az 1–8 skálán
  const w = 260, h = 70, pad = 14, bw = (w - 2 * pad) / 8, d = p.eqf.distribution, max = Math.max(...Object.values(d), 0.01);
  let s = `<svg width="${w}" height="${h + 16}" viewBox="0 0 ${w} ${h + 16}" role="img" aria-label="TESZ-SZÍN-ek EQF-eloszlása">`;
  s += `<line class="axis" x1="${pad}" x2="${w - pad}" y1="${h}" y2="${h}"/>`;
  for (let n = 1; n <= 8; n++) {
    const v = d[String(n)] || 0, bh = (v / max) * (h - 8), x = pad + (n - 1) * bw + 3;
    if (v > 0) s += `<rect class="dist" x="${x}" y="${h - bh}" width="${bw - 6}" height="${bh}" rx="3" data-tip="EQF ${n}: ${v.toFixed(2)} súlyozott TESZ-SZÍN" opacity="${n === p.eqf.typical ? 1 : 0.45}"/>`;
    s += `<text x="${x + (bw - 6) / 2}" y="${h + 13}" text-anchor="middle">${n}</text>`;
  }
  return s + '</svg>';
}

function jobDetail(p) {
  const key = 'job:' + p.id, all = p.competencies, limit = state.showAll[key] ? all.length : 25;
  const grouped = groupBy(all.slice(0, limit), 'category_label');
  const tesz = Object.fromEntries(p.tesz.map((t) => [t.id, t]));
  let rows = '';
  for (const [cat, items] of grouped) {
    rows += `<tr class="cat"><td colspan="4">${esc(cat)}</td></tr>`;
    for (const c of items) {
      rows += `<tr class="clickable" data-expand="${esc(c.id)}" tabindex="0"><td>${esc(c.label)}<span class="sub muted small" style="display:block">${esc(c.group_label)}</span></td>
        <td style="width:22%"><div class="fitcell">${bar(c.importance, 'thin')}<b class="small">${pct(c.importance)}</b></div></td>
        <td style="width:190px" data-tip="Elvárt EQF: ${c.required_eqf ?? '–'} (az őt igénylő ${c.core ? 'alap' : 'támogató'} TESZ-SZÍN-ek legmagasabb szintje)">${eqfScale({ req: c.required_eqf })}</td>
        <td class="num small muted" style="text-align:right">${c.tesz.length} TESZ</td></tr>
        <tr hidden data-detail="${esc(c.id)}"><td colspan="4"><ul class="tesz-list">${c.tesz.map((id) => tesz[id]).filter(Boolean).map((t) => `<li>${eqfBadge(t.eqf)}<span><b>${esc(t.label)}</b> · ${REL[t.relevance] || ''}, ${FREQ[t.frequency] || ''}${t.kozeg ? ' · ' + esc(t.kozeg) : ''}${t.helyzet ? ' / ' + esc(t.helyzet) : ''}${t.kontextus ? `<br><span class="muted">${esc(t.kontextus)}</span>` : ''}</span></li>`).join('')}</ul></td></tr>`;
    }
  }
  return `<div class="card"><div class="card-head"><div><span class="eyebrow">MUNKAKÖR-PROFIL</span><h2>${esc(p.title)}</h2>${p.purpose ? `<p class="small muted" style="max-width:760px;margin:6px 0 0">${esc(p.purpose)}</p>` : ''}</div>
      <div data-tip="A TESZ-SZÍN-ek súlyozott EQF-eloszlása (súly: alap 1 / támogató 0,5 × gyakoriság). A kiemelt oszlop a tipikus szint.">${eqfDistribution(p)}</div></div>
    <div class="stats"><div class="stat"><strong>EQF ${p.eqf.typical ?? '–'}</strong><span>tipikus szint (súlyozott medián)</span></div>
      <div class="stat"><strong>${p.eqf.min ?? '–'}–${p.eqf.max ?? '–'}</strong><span>EQF-terjedelem a feladatokban</span></div>
      <div class="stat"><strong>${p.tesz.length}</strong><span>TESZ-SZÍN (feladatváltozat)</span></div>
      <div class="stat"><strong>${all.length}</strong><span>kompetencia</span></div></div>
    <h3 style="margin-top:18px">Kompetenciák a TESZ-nivalókon keresztül <span class="muted small" style="font-weight:400">· kattints egy sorra a TESZ-nivalókért</span></h3>
    <div class="legend"><span><span class="sw" style="background:var(--accent)"></span>elvárt EQF-szint</span><span>fontosság = az őt igénylő TESZ-SZÍN-ek súlya a legfontosabbhoz mérve</span></div>
    <div class="scroll-x"><table><thead><tr><th>Kompetencia</th><th>Fontosság</th><th>Elvárt EQF (1–8)</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>
    ${all.length > 25 ? `<button type="button" class="link more" data-more="${esc(key)}">${state.showAll[key] ? 'Csak a 25 legfontosabb' : `Mind a ${all.length} kompetencia`}</button>` : ''}</div>`;
}

function wireJobStage(stage) {
  stage.querySelectorAll('[data-col]').forEach((th) => (th.onclick = th.onkeydown = (e) => {
    if (e.type === 'keydown' && e.key !== 'Enter') return;
    state.active = th.dataset.col; save(); renderContext(); renderJobStage();
  }));
  stage.querySelectorAll('[data-more]').forEach((b) => (b.onclick = () => { state.showAll[b.dataset.more] = !state.showAll[b.dataset.more]; renderJobStage(); }));
  wireExpand(stage);
}
function wireExpand(root) {
  root.querySelectorAll('[data-expand]').forEach((tr) => (tr.onclick = tr.onkeydown = (e) => {
    if (e.type === 'keydown' && e.key !== 'Enter') return;
    const d = root.querySelector(`[data-detail="${CSS.escape(tr.dataset.expand)}"]`); if (d) d.hidden = !d.hidden;
  }));
}

// ------------------------------------------------------------------ 2. egyéni profil
/* Öt forrás: explicit, implicit, munkakör (örökölt a gráfból vagy AI-következtetés), képzés, stílus; plusz kézi.
   A forrástípusok ki-be kapcsolhatók: a kikapcsolt forrásból (csak onnan) származó kompetencia nem számít bele
   a 3. és 4. funkcióba. */
const SOURCES = {
  EXPLICIT: ['explicit', 'A szöveg kimondja a kompetenciát.'],
  IMPLICIT: ['implicit', 'A leírt tevékenységből, eredményből, felelősségből következik.'],
  MUNKAKOR: ['munkakör', 'A betöltött munkakörből: felbontott munkakörnél a gráfprofil alapfeladataiból örökölve, egyébként AI-következtetés.'],
  KEPZES: ['képzés', 'A megemlített képzésből vagy végzettségből.'],
  STILUS: ['stílus', 'A dokumentum általános stílusából (felépítés, fogalmazás, pontosság); legfeljebb közepes bizonyosság, szint nélkül.'],
  KEZI: ['kézi', 'Kézzel felvett vagy régebbi profilból betöltött kompetencia.'],
};

function normPerson(p) {
  // régi (forrás nélküli) profil: a bizonyítékos elem explicit, a többi kézi
  if (!p || !Array.isArray(p.competencies)) return null;
  p.off = Array.isArray(p.off) ? p.off : [];
  p.experience = p.experience || []; p.trainings = p.trainings || [];
  for (const c of p.competencies) {
    if (!Array.isArray(c.types) || !c.types.length) {
      c.types = [c.evidence?.length ? 'EXPLICIT' : 'KEZI'];
      c.sources = c.sources || [{ type: c.types[0], confidence: c.confidence ?? null, eqf: c.eqf ?? null, evidence: c.evidence || [], reason: c.reason || '' }];
    }
  }
  return p;
}
function isActive(c, p = state.person) { const off = new Set(p?.off || []); return c.types.some((t) => !off.has(t)); }
function activeComps() { return (state.person?.competencies || []).filter((c) => isActive(c)); }

document.querySelectorAll('[data-identify-person]').forEach((b) => (b.onclick = async () => {
  const src = b.dataset.identifyPerson;
  b.disabled = true;
  try {
    const body = await documentBody(src === 'file' ? 'person-file' : null, src === 'text' ? 'person-text' : null);
    if (state.person?.competencies?.length && !confirm('A meglévő egyéni profil helyére az új elemzés kerül. Folytatod?')) return;
    setStatus('person-status', 'Kompetenciák kigyűjtése öt forrásból és EQF-becslés az AI-val… (fél–két perc)');
    const d = await api('lab/identify-person', body);
    const file = $('person-file').files[0];
    state.person = normPerson({
      source: src === 'file' && file ? file.name : 'Beillesztett szöveg',
      eqf: d.eqf?.level ?? null, eqf_rationale: d.eqf?.rationale || '', warnings: d.warnings,
      experience: d.work_experience, trainings: d.trainings, off: [],
      competencies: d.competencies.map((c) => ({ id: c.id, label: c.label, eqf: c.eqf ?? null, confidence: c.confidence, types: c.types, sources: c.sources, category_label: c.category_label, group_label: c.group_label })),
    });
    const n = d.counts;
    setStatus('person-status', d.competencies.length
      ? `${d.competencies.length} kompetencia: ${Object.entries(n).filter(([, v]) => v).map(([k, v]) => `${SOURCES[k][0]} ${v}`).join(', ')}. Ellenőrizd a forrásokat, és szükség esetén módosítsd a szinteket.`
      : 'Nem találtunk alátámasztott kompetenciát.');
    changed('person');
  } catch (e) { setStatus('person-status', e.message, true); } finally { b.disabled = false; }
}));

function renderSkillList() {
  const q = $('skill-search').value.trim().toLocaleLowerCase('hu'), have = new Set((state.person?.competencies || []).map((c) => c.id));
  const items = Object.values(state.catalog).filter((c) => !q || (c.label + ' ' + c.label_en).toLocaleLowerCase('hu').includes(q)).slice(0, 200);
  $('skill-list').innerHTML = items.map((c) => `<li class="${have.has(c.id) ? 'on' : ''}"><span class="t">${esc(c.label)}<small>${esc(c.group_label)}</small></span><button type="button" class="icon" data-skill="${esc(c.id)}" ${have.has(c.id) ? 'disabled' : ''}>${have.has(c.id) ? '✓' : '+'}</button></li>`).join('');
  $('skill-list').querySelectorAll('[data-skill]').forEach((b) => (b.onclick = () => {
    state.person = state.person || normPerson({ source: 'Kézi profil', eqf: null, competencies: [] });
    const k = state.catalog[b.dataset.skill];
    state.person.competencies.push({ id: k.id, label: k.label, eqf: state.person.eqf ?? null, confidence: null, types: ['KEZI'],
      sources: [{ type: 'KEZI', reason: 'Kézzel felvéve.', evidence: [] }], category_label: k.category_label, group_label: k.group_label });
    changed('person');
  }));
}
$('skill-search').oninput = renderSkillList;

function eqfSelect(value, attrs) {
  return `<select ${attrs} style="width:auto;display:inline-block;margin:0;padding:3px 6px">` + ['', 1, 2, 3, 4, 5, 6, 7, 8].map((n) => `<option value="${n}" ${String(value ?? '') === String(n) ? 'selected' : ''}>${n === '' ? '–' : n}</option>`).join('') + '</select>';
}
function srcTag(t) { return `<span class="src" data-tip="<b>${esc(SOURCES[t][0])}</b>${esc(SOURCES[t][1])}">${esc(SOURCES[t][0])}</span>`; }

function sourceDetail(s) {
  const head = `${srcTag(s.type)}${s.ref_label ? ` <b>${esc(s.ref_label)}</b>` : ''}${s.inherited ? ' <span class="muted">· a gráfprofilból örökölve</span>' : ''}${s.eqf ? ' ' + eqfBadge(s.eqf) : ''}${s.confidence != null ? ` <span class="muted">· ${pct(s.confidence)}</span>` : ''}`;
  return `<div class="srcd"><div>${head}</div>${s.reason ? `<p>${esc(s.reason)}</p>` : ''}${(s.evidence || []).map((q) => `<blockquote>„${esc(q)}”</blockquote>`).join('')}</div>`;
}

function renderPerson() {
  const stage = $('person-stage'), p = state.person;
  if (!p || !p.competencies.length) {
    stage.innerHTML = `<div class="empty"><h2>Egyéni profil</h2><p>Tölts fel önéletrajzot, illessz be szöveget, vagy válaszd ki kézzel a kompetenciákat.<br>Az AI öt forrásból gyűjt: <b>explicit</b> és <b>implicit</b> említés, betöltött <b>munkakörök</b>, <b>képzések</b>, és a dokumentum <b>stílusa</b>. A kompetenciának nem kell szó szerint szerepelnie.</p></div>`;
    return;
  }
  const off = new Set(p.off), act = activeComps();
  const counts = Object.fromEntries(Object.keys(SOURCES).map((t) => [t, p.competencies.filter((c) => c.types.includes(t)).length]));
  const toggles = Object.keys(SOURCES).filter((t) => counts[t]).map((t) => `<label class="toggle" data-tip="${esc(SOURCES[t][1])}"><input type="checkbox" data-src-toggle="${t}" ${off.has(t) ? '' : 'checked'}>${esc(SOURCES[t][0])} <span class="muted">${counts[t]}</span></label>`).join('');
  const rows = [...p.competencies].sort((a, b) => (b.confidence ?? 0) - (a.confidence ?? 0));
  const levels = act.map((c) => c.eqf).filter(Boolean);
  let body = '';
  for (const [cat, items] of groupBy(rows, 'category_label')) {
    body += `<tr class="cat"><td colspan="6">${esc(cat)}</td></tr>`;
    for (const c of items) {
      const on = isActive(c);
      body += `<tr class="${on ? '' : 'off'}"><td>${esc(c.label || compLabel(c.id))}<span class="muted small" style="display:block">${esc(c.group_label || '')}</span></td>
        <td>${c.types.map(srcTag).join(' ')}</td>
        <td>${eqfSelect(c.eqf, `data-ceqf="${esc(c.id)}" aria-label="EQF-szint"`)}</td>
        <td style="width:180px">${c.eqf ? eqfScale({ own: c.eqf }) : '<span class="muted small">szint nélkül: az általános szinted számít</span>'}</td>
        <td class="small muted num">${c.confidence != null ? `<span data-tip="Összesített bizonyosság: 1 − Π(1 − forrásonkénti bizonyosság)">${pct(c.confidence)}</span>` : 'kézi'}</td>
        <td style="text-align:right;white-space:nowrap"><button type="button" class="link" data-ev="${esc(c.id)}">források</button> <button type="button" class="icon" data-del="${esc(c.id)}" aria-label="Eltávolítás">×</button></td></tr>
        <tr hidden data-evd="${esc(c.id)}"><td colspan="6">${(c.sources || []).map(sourceDetail).join('')}</td></tr>`;
    }
  }
  const sel = new Set(state.jobs.map((j) => j.id));
  const exp = p.experience.length ? `<div class="card"><span class="eyebrow">MUNKATAPASZTALAT</span><h2>Betöltött munkakörök</h2><table><thead><tr><th>A dokumentumban</th><th>Évek</th><th>Megfeleltetett munkakör</th><th></th></tr></thead><tbody>${p.experience.map((e) => `<tr>
      <td>${esc(e.title)}<span class="muted small" style="display:block">„${esc(e.evidence?.[0] || '')}”</span></td><td class="num">${e.years != null ? esc(e.years) : '–'}</td>
      <td>${e.job_id ? `${esc(e.job_title)} ${eqfBadge(e.job_eqf)}<span class="muted small" style="display:block">${e.inherited} kompetencia örökölve a gráfprofilból</span>` : '<span class="muted small">nincs felbontott megfelelője; az AI következtetett</span>'}</td>
      <td>${e.job_id ? `<button type="button" class="secondary" data-pick-exp="${esc(e.job_id)}" data-title="${esc(e.job_title)}" data-eqf="${e.job_eqf ?? ''}" ${sel.has(e.job_id) ? 'disabled' : ''}>${sel.has(e.job_id) ? 'kiválasztva' : '+ összevetéshez'}</button>` : ''}</td></tr>`).join('')}</tbody></table></div>` : '';
  const tr = p.trainings.length ? `<div class="card"><span class="eyebrow">KÉPZÉSEK</span><h2>Végzettségek és képzések</h2><table><thead><tr><th>Képzés</th><th>EQF</th><th>Kompetenciák</th></tr></thead><tbody>${p.trainings.map((t) => `<tr>
      <td>${esc(t.name)}<span class="muted small" style="display:block">„${esc(t.evidence?.[0] || '')}”</span></td><td>${eqfBadge(t.eqf)}</td>
      <td class="small">${p.competencies.filter((c) => (c.sources || []).some((s) => s.type === 'KEPZES' && s.ref_label === t.name)).map((c) => esc(c.label)).join(', ') || '<span class="muted">–</span>'}</td></tr>`).join('')}</tbody></table></div>` : '';
  stage.innerHTML = `<div class="card"><div class="card-head"><div><span class="eyebrow">EGYÉNI PROFIL</span><h2>${esc(p.source || 'Egyéni profil')}</h2></div></div>
    <div class="stats"><div class="stat"><strong>${act.length}${act.length < p.competencies.length ? `<small class="muted" style="font-size:14px"> / ${p.competencies.length}</small>` : ''}</strong><span>figyelembe vett kompetencia</span></div>
      <div class="stat"><strong>EQF ${eqfSelect(p.eqf, 'id="person-eqf" aria-label="Általános EQF-szint"')}</strong><span>általános szint (becslés, módosítható)</span></div>
      <div class="stat"><strong>${levels.length ? Math.min(...levels) + '–' + Math.max(...levels) : '–'}</strong><span>kompetenciánkénti EQF-terjedelem</span></div></div>
    ${p.eqf_rationale ? `<p class="small" style="margin:12px 0 0"><b>A szintbecslés indoklása:</b> ${esc(p.eqf_rationale)}</p>` : ''}
    <div class="toggles"><span class="small muted">Források a profilban:</span>${toggles}</div>
    ${(p.warnings || []).length ? `<details class="notes"><summary>Megjegyzések az elemzéshez (${p.warnings.length})</summary>${p.warnings.map((w) => `<p class="warning">${esc(w)}</p>`).join('')}</details>` : ''}
    <div class="scroll-x" style="margin-top:10px"><table><thead><tr><th>Kompetencia</th><th>Forrás</th><th>EQF</th><th>Szint (1–8)</th><th>Bizonyosság</th><th></th></tr></thead><tbody>${body}</tbody></table></div></div>
    ${exp}${tr}`;
  $('person-eqf').onchange = (e) => { p.eqf = e.target.value ? Number(e.target.value) : null; changed('person'); };
  stage.querySelectorAll('[data-src-toggle]').forEach((x) => (x.onchange = () => {
    const s = new Set(p.off); x.checked ? s.delete(x.dataset.srcToggle) : s.add(x.dataset.srcToggle); p.off = [...s]; changed('person');
  }));
  stage.querySelectorAll('[data-ceqf]').forEach((s) => (s.onchange = () => { const c = p.competencies.find((x) => x.id === s.dataset.ceqf); c.eqf = s.value ? Number(s.value) : null; changed('person'); }));
  stage.querySelectorAll('[data-del]').forEach((b) => (b.onclick = () => { p.competencies = p.competencies.filter((x) => x.id !== b.dataset.del); if (!p.competencies.length) state.person = null; changed('person'); }));
  stage.querySelectorAll('[data-ev]').forEach((b) => (b.onclick = () => { const r = stage.querySelector(`[data-evd="${CSS.escape(b.dataset.ev)}"]`); r.hidden = !r.hidden; }));
  stage.querySelectorAll('[data-pick-exp]').forEach((b) => (b.onclick = () => { if (addJob({ id: b.dataset.pickExp, title: b.dataset.title, eqf: b.dataset.eqf ? Number(b.dataset.eqf) : null })) renderPerson(); }));
}

$('person-export').onclick = () => {
  if (!state.person) { setStatus('person-status', 'Nincs menthető profil.', true); return; }
  const url = URL.createObjectURL(new Blob([JSON.stringify(state.person, null, 2)], { type: 'application/json' }));
  const a = document.createElement('a'); a.href = url; a.download = 'gaphopper-egyeni-profil.json'; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
};
$('person-import').onchange = async (e) => {
  try {
    const d = normPerson(JSON.parse(await e.target.files[0].text()));
    if (!d) throw new Error('Hiányzó competencies lista.');
    const unknown = d.competencies.filter((c) => !state.catalog[c.id]).map((c) => c.id);
    d.competencies = d.competencies.filter((c) => state.catalog[c.id]);
    state.person = d; changed('person');
    setStatus('person-status', `Betöltve: ${d.competencies.length} kompetencia${unknown.length ? `; ${unknown.length} ismeretlen azonosító kimaradt` : ''}.`);
  } catch (err) { setStatus('person-status', 'A fájl nem olvasható: ' + err.message, true); }
  e.target.value = '';
};

function pastTag(jobId) {
  // korábbi munkakör: a CV szerint betöltötte; bekapcsolt munkakör-forrásnál a kompetenciái öröklődtek, ezért magas az illeszkedés
  const p = state.person;
  if (!p?.experience?.some((e) => e.job_id === jobId)) return '';
  const inh = !(p.off || []).includes('MUNKAKOR');
  return ` <span class="src" data-tip="<b>Korábbi munkaköröd</b>${inh ? 'A kompetenciái a profilodba öröklődtek, ezért az illeszkedés ehhez a munkakörhöz természetesen magas. A 2. fülön a munkakör-forrás kikapcsolható.' : 'A munkakör-forrás ki van kapcsolva: csak a közvetlen bizonyítékok számítanak.'}">korábbi munkaköröd</span>`;
}

function personPayload() {
  // csak a bekapcsolt forrásokból származó kompetenciák
  return { competencies: activeComps().map((c) => ({ id: c.id, eqf: c.eqf ?? null })), eqf: state.person.eqf ?? null };
}

// ------------------------------------------------------------------ 3. ajánló
$('rec-run').onclick = () => { state.rec = null; runRecommend(); };
$('rec-window').onchange = $('rec-limit').onchange = () => { state.rec = null; runRecommend(); };
async function runRecommend() {
  const stage = $('rec-stage');
  if (!activeComps().length) { stage.innerHTML = '<div class="empty"><h2>Előbb készíts egyéni profilt</h2><p>Az ajánló a 2. fül kompetenciáiból és EQF-szintjeiből dolgozik.</p></div>'; return; }
  if (state.rec) { renderRecommend(); return; }
  setStatus('rec-status', 'Számolás…');
  try {
    const w = $('rec-window').value;
    if (w && !state.person.eqf) setStatus('rec-status', 'Az EQF-szűréshez add meg az általános EQF-szintet a 2. fülön; most szűrés nélkül számolunk.');
    state.rec = await api('lab/recommendations', { person: personPayload(), limit: Number($('rec-limit').value), eqf_window: w ? Number(w) : null });
    if (!(w && !state.person.eqf)) setStatus('rec-status', `${state.rec.total} felbontott munkakör vizsgálva${w ? `, ebből ${state.rec.filtered} esik a szintablakba` : ''}.`);
    renderRecommend();
  } catch (e) { setStatus('rec-status', e.message, true); }
}

function scatter(points, personEqf) {
  // x: illeszkedés (0–1), y: a munkakör tipikus EQF-szintje (1–8); a kiemelt pontok az ajánlottak
  const W = 760, H = 310, L = 36, R = 16, T = 24, B = 34;
  const x = (v) => L + v * (W - L - R), y = (v) => T + (8 - v) / 7 * (H - T - B);
  const jit = (id) => { let h = 0; for (const ch of id) h = (h * 31 + ch.charCodeAt(0)) | 0; return ((h % 1000) / 1000 - 0.5) * 0.5; };
  let s = `<svg viewBox="0 0 ${W} ${H}" width="100%" style="max-width:${W}px" role="img" aria-label="Munkakörök illeszkedés és EQF-szint szerint">`;
  for (let v = 1; v <= 8; v++) s += `<line class="grid" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/><text x="${L - 8}" y="${y(v) + 3}" text-anchor="end">${v}</text>`;
  for (let v = 0; v <= 1.0001; v += 0.25) s += `<text x="${x(v)}" y="${H - B + 16}" text-anchor="middle">${Math.round(v * 100)}%</text>`;
  s += `<line class="axis" x1="${L}" x2="${W - R}" y1="${H - B}" y2="${H - B}"/>`;
  s += `<text x="${(L + W - R) / 2}" y="${H - 2}" text-anchor="middle">illeszkedés →</text><text x="${L - 8}" y="10" text-anchor="end">EQF ↑</text>`;
  if (personEqf) s += `<line class="ref" x1="${L}" x2="${W - R}" y1="${y(personEqf)}" y2="${y(personEqf)}"/><text x="${W - R}" y="${y(personEqf) - 5}" text-anchor="end" style="fill:var(--ink-2)">saját szint (EQF ${personEqf})</text>`;
  const sorted = [...points].sort((a, b) => a.selected - b.selected);
  for (const p of sorted) {
    if (p.eqf == null) continue;
    const cx = x(p.fit), cy = y(p.eqf + jit(p.job_id));
    const t = `<b>${esc(p.title)}</b>illeszkedés: ${pct(p.fit)} · lefedettség: ${pct(p.coverage)}<br>tipikus EQF: ${p.eqf}`;
    s += `<g data-tip="${esc(t)}" data-pt="${esc(p.job_id)}"><circle class="hit" cx="${cx}" cy="${cy}" r="10"/><circle class="${p.selected ? 'dot-sel' : 'dot-bg'}" cx="${cx}" cy="${cy}" r="${p.selected ? 5.5 : 3.5}"/></g>`;
  }
  return s + '</svg>';
}

function missingChips(list, n = 3) {
  return list.slice(0, n).map((c) => `<span class="st soft ${c.status}" style="margin:0 4px 4px 0" data-tip="<b>${esc(c.label)}</b>${esc(STATUS[c.status][1])} · elvárt EQF ${c.required_eqf ?? '–'}${c.person_eqf ? ', saját ' + c.person_eqf : ''}">${STATUS[c.status][0]} ${esc(c.label)}</span>`).join('') + (list.length > n ? `<span class="muted small">+${list.length - n}</span>` : '');
}

function renderRecommend() {
  const d = state.rec, sel = new Set(state.jobs.map((j) => j.id)), pe = state.person?.eqf;
  const delta = (g) => g.eqf.typical == null || !pe ? '' : g.eqf.typical > pe ? `<span class="small" data-tip="A munkakör tipikus szintje ${g.eqf.typical - pe} szinttel a tiéd fölött">▲ +${g.eqf.typical - pe}</span>` : g.eqf.typical < pe ? `<span class="small muted" data-tip="A munkakör tipikus szintje a tiéd alatt">▼ ${g.eqf.typical - pe}</span>` : '<span class="small muted">= szint</span>';
  const rows = d.items.map((g, i) => `<tr><td class="num muted">${i + 1}</td>
      <td><b>${esc(g.title)}</b>${pastTag(g.job_id)}<span class="muted small" style="display:block">${esc(BAND[g.band])}</span></td>
      <td style="white-space:nowrap">${eqfBadge(g.eqf.typical)} ${delta(g)}</td>
      <td style="min-width:150px"><div class="fitcell">${bar(g.fit)}<b>${pct(g.fit)}</b></div><span class="muted small">lefedettség ${pct(g.coverage)}</span></td>
      <td>${g.missing.length ? missingChips(g.missing) : '<span class="st ok">✓ minden megvan</span>'}</td>
      <td><button type="button" class="secondary" data-pick="${esc(g.job_id)}" data-title="${esc(g.title)}" data-eqf="${g.eqf.typical ?? ''}" ${sel.has(g.job_id) ? 'disabled' : ''}>${sel.has(g.job_id) ? 'kiválasztva' : '+ összevetéshez'}</button></td></tr>`).join('');
  $('rec-stage').innerHTML = `<div class="card"><div class="card-head"><div><span class="eyebrow">TÉRKÉP</span><h2>Hol állsz a munkakörök között?</h2><p class="small muted" style="margin:4px 0 0">Minden pont egy felbontott munkakör: jobbra az illeszkedés nő, felfelé az elvárt EQF-szint.</p></div>
      <div class="legend"><span><span class="sw" style="background:var(--accent)"></span>ajánlott (${d.items.length})</span><span><span class="sw" style="background:var(--dot-off)"></span>többi munkakör</span>${pe ? '<span>— saját EQF-szint</span>' : ''}</div></div>
      ${scatter(d.scatter, pe)}</div>
    <div class="card"><div class="card-head"><div><span class="eyebrow">AJÁNLÁS</span><h2>A legjobban illeszkedő munkakörök</h2></div><span class="muted small">${d.total} munkakör vizsgálva</span></div>
      <div class="scroll-x"><table><thead><tr><th>#</th><th>Munkakör</th><th>EQF</th><th>Illeszkedés</th><th>Legfontosabb hiányok (▽ szint alatt · ✕ hiányzik)</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>
      <p class="small muted">Tipp: a „+ összevetéshez” gombbal a munkakör a felső sávba kerül, és a 4. fülön részletesen összevethető.</p></div>`;
  $('rec-stage').querySelectorAll('[data-pick]').forEach((b) => (b.onclick = () => { if (addJob({ id: b.dataset.pick, title: b.dataset.title, eqf: b.dataset.eqf ? Number(b.dataset.eqf) : null })) { b.disabled = true; b.textContent = 'kiválasztva'; } }));
  $('rec-stage').querySelectorAll('[data-pt]').forEach((g) => (g.onclick = () => { const it = d.scatter.find((p) => p.job_id === g.dataset.pt); addJob({ id: it.job_id, title: it.title, eqf: it.eqf }); renderRecommend(); }));
}

// ------------------------------------------------------------------ 4. illeszkedés és hiány
$('gap-run').onclick = () => { state.gap = null; runGap(); };
async function runGap() {
  const stage = $('gap-stage');
  const missing = [!activeComps().length && 'egyéni profil (2. fül, legalább egy bekapcsolt forrással)', !state.jobs.length && 'legalább egy munkakör (1. vagy 3. fül)'].filter(Boolean);
  if (missing.length) { stage.innerHTML = `<div class="empty"><h2>Hiányzik: ${missing.join(' és ')}</h2><p>Az összevetéshez egyéni profil és kiválasztott munkakör kell.</p></div>`; return; }
  if (state.gap) { renderGap(); return; }
  setStatus('gap-status', 'Számolás…');
  try {
    state.gap = await api('lab/gap', { job_ids: state.jobs.map((j) => j.id), person: personPayload() });
    setStatus('gap-status', '');
    renderGap();
  } catch (e) { setStatus('gap-status', e.message, true); }
}

function renderGap() {
  const d = state.gap, pe = d.person_eqf, jobs = [...d.jobs].sort((a, b) => b.fit - a.fit);
  if (!jobs.some((j) => j.job_id === state.gapActive)) state.gapActive = jobs.some((j) => j.job_id === state.active) ? state.active : jobs[0].job_id;
  const act = jobs.find((j) => j.job_id === state.gapActive);
  const dEqf = (g) => g.eqf_delta == null ? '<span class="muted small">–</span>' : g.eqf_delta > 0 ? `<span data-tip="A munkakör tipikus szintje ${g.eqf_delta} szinttel a tiéd fölött">▲ +${g.eqf_delta}</span>` : g.eqf_delta < 0 ? `<span class="muted" data-tip="A munkakör tipikus szintje a tiéd alatt">▼ ${g.eqf_delta}</span>` : '<span class="muted">=</span>';
  const summary = jobs.map((g) => `<tr class="clickable ${g.job_id === state.gapActive ? 'sel' : ''}" data-gjob="${esc(g.job_id)}" tabindex="0">
      <td><b>${esc(g.title)}</b>${pastTag(g.job_id)}${g.job_id === state.gapActive ? ' <span class="muted small">· részletezve lent</span>' : ''}</td>
      <td style="min-width:170px"><div class="fitcell">${bar(g.fit)}<b>${pct(g.fit)}</b></div></td>
      <td class="num">${g.distance.toFixed(2)}</td><td class="num">${pct(g.coverage)}</td>
      <td style="white-space:nowrap">${eqfBadge(g.eqf.typical)} <span class="small">${dEqf(g)}</span></td>
      <td style="white-space:nowrap">${stPill('ok', g.counts.ok)} ${stPill('under', g.counts.under)} ${stPill('missing', g.counts.missing)}</td></tr>`).join('');
  $('gap-stage').innerHTML = `<div class="card"><div class="card-head"><div><span class="eyebrow">TÁVOLSÁG</span><h2>Mennyire vagy közel a kiválasztott munkakörökhöz?</h2></div>${pe ? `<span class="small">saját szint: ${eqfBadge(pe)}</span>` : '<span class="small muted">Általános EQF-szint nincs megadva</span>'}</div>
      <div class="scroll-x"><table><thead><tr><th>Munkakör</th><th data-tip="1 − távolság">Illeszkedés</th><th data-tip="Súlyozott hiány 0 (nincs) – 1 (teljes)">Távolság</th><th data-tip="A munkakör súlyozott kompetenciáiból mennyi van meg (szinttől függetlenül)">Lefedettség</th><th>EQF (eltérés)</th><th>Kompetenciák</th></tr></thead><tbody>${summary}</tbody></table></div>
      <div class="legend">${stPill('ok')} megvan, legalább az elvárt szinten ${stPill('under')} megvan, de az elvártnál alacsonyabb szinten ${stPill('missing')} hiányzik</div></div>
    ${jobs.length > 1 ? gapMatrix(jobs, pe) : ''}
    ${act ? gapDetail(act) : ''}
    ${d.extra.length ? `<div class="card"><span class="eyebrow">TÖBBLET</span><h2>Kompetenciáid, amelyeket egyik kiválasztott munkakör sem igényel</h2><div class="chips">${d.extra.map((c) => `<span class="chip" style="padding-right:10px"><span class="t">${esc(c.label)}</span>${c.eqf ? eqfBadge(c.eqf) : ''}</span>`).join('')}</div></div>` : ''}`;
  const st = $('gap-stage');
  st.querySelectorAll('[data-gjob]').forEach((tr) => (tr.onclick = tr.onkeydown = (e) => { if (e.type === 'keydown' && e.key !== 'Enter') return; state.gapActive = tr.dataset.gjob; renderGap(); }));
  st.querySelectorAll('[data-more]').forEach((b) => (b.onclick = () => { state.showAll[b.dataset.more] = !state.showAll[b.dataset.more]; renderGap(); }));
  wireExpand(st);
}

function gapMatrix(jobs, pe) {
  const rows = new Map(), own = Object.fromEntries(activeComps().map((c) => [c.id, c.eqf]));
  for (const g of jobs) for (const c of g.competencies) {
    const r = rows.get(c.id) || { id: c.id, label: c.label, category_label: c.category_label, group_label: c.group_label, by: {}, top: 0, bad: 0 };
    r.by[g.job_id] = c; r.top = Math.max(r.top, c.importance); r.bad += c.status === 'ok' ? 0 : c.weight * c.shortfall; rows.set(c.id, r);
  }
  // elöl a sok munkakörben fontos hiányok, utána a többi
  const all = [...rows.values()].sort((a, b) => b.bad - a.bad || b.top - a.top);
  const key = 'gapm', limit = state.showAll[key] ? all.length : 30, shown = new Set(all.slice(0, limit).map((r) => r.id));
  let body = '';
  for (const [cat, items] of groupBy(all.filter((r) => shown.has(r.id)), 'category_label')) {
    body += `<tr class="cat"><td colspan="${jobs.length + 2}">${esc(cat)}</td></tr>`;
    for (const r of items) {
      const mine = r.id in own ? (own[r.id] ?? pe ?? '✓') : '–';
      body += `<tr><td class="lab">${esc(r.label)}<span class="sub">${esc(r.group_label)}</span></td><td class="cell num"><b>${esc(mine)}</b></td>` + jobs.map((g) => {
        const c = r.by[g.job_id];
        if (!c) return '<td class="cell"><span class="muted">·</span></td>';
        return `<td class="cell"><span class="st ${c.status}" tabindex="0" data-tip="<b>${esc(r.label)}</b>${esc(g.title)}<br>${esc(STATUS[c.status][1])} · elvárt EQF ${c.required_eqf ?? '–'}${c.person_eqf ? ', saját ' + c.person_eqf : ''}<br>fontosság ${pct(c.importance)}">${STATUS[c.status][0]} ${c.required_eqf ?? ''}</span></td>`;
      }).join('') + '</tr>';
    }
  }
  return `<div class="card"><div class="card-head"><div><span class="eyebrow">KOMPETENCIA-MÁTRIX</span><h2>Hiányok munkakörönként</h2><p class="small muted" style="margin:4px 0 0">A cellában az elvárt EQF-szint; a „Saját” oszlopban a te szinted. Elöl a több munkakörben is fontos hiányok.</p></div></div>
    <div class="scroll-x"><table class="matrix"><thead><tr><th>Kompetencia</th><th class="job" style="cursor:default">Saját<br>${eqfBadge(pe)}</th>${jobs.map((g) => `<th class="job ${g.job_id === state.gapActive ? 'active' : ''}" data-gjob="${esc(g.job_id)}" tabindex="0">${esc(g.title)}<br><span class="muted small">${pct(g.fit)}</span></th>`).join('')}</tr></thead><tbody>${body}</tbody></table></div>
    ${all.length > 30 ? `<button type="button" class="link more" data-more="${key}">${state.showAll[key] ? 'Csak az első 30' : `Mind a ${all.length} kompetencia`}</button>` : ''}</div>`;
}

function gapDetail(g) {
  const order = { missing: 0, under: 1, ok: 2 };
  const all = [...g.competencies].sort((a, b) => order[a.status] - order[b.status] || b.weight * b.shortfall - a.weight * a.shortfall || b.importance - a.importance);
  const key = 'gapd:' + g.job_id, limit = state.showAll[key] ? all.length : 25;
  const rows = all.slice(0, limit).map((c) => `<tr class="clickable" data-expand="g-${esc(c.id)}" tabindex="0"><td>${esc(c.label)}<span class="muted small" style="display:block">${esc(c.category_label)}</span></td>
      <td style="width:22%"><div class="fitcell">${bar(c.importance, 'thin')}<b class="small">${pct(c.importance)}</b></div></td>
      <td style="width:190px" data-tip="saját: ${c.person_eqf ?? 'nincs'} · elvárt: ${c.required_eqf ?? '–'}">${eqfScale({ req: c.required_eqf, own: c.person_eqf, status: c.status, showOwn: true })}</td>
      <td>${stPill(c.status)} <span class="small">${esc(STATUS[c.status][1])}</span></td></tr>
      <tr hidden data-detail="g-${esc(c.id)}"><td colspan="4" class="small muted">${c.status === 'missing' ? `Nincs a profilodban. Az elvárt szint EQF ${c.required_eqf ?? '–'}; ${c.tesz.length} TESZ-SZÍN igényli a munkakörben.` : c.status === 'under' ? `A te szinted EQF ${c.person_eqf}, az elvárt EQF ${c.required_eqf}: ${c.required_eqf - c.person_eqf} szint hiányzik.` : `A te szinted (EQF ${c.person_eqf}) eléri az elvártat (EQF ${c.required_eqf ?? '–'}).`}</td></tr>`).join('');
  return `<div class="card"><div class="card-head"><div><span class="eyebrow">RÉSZLETES HIÁNY</span><h2>${esc(g.title)}</h2></div>
      <div class="stats" style="flex:1 1 360px;max-width:560px"><div class="stat"><strong>${pct(g.fit)}</strong><span>illeszkedés</span></div><div class="stat"><strong>${pct(g.coverage)}</strong><span>lefedettség</span></div><div class="stat"><strong>${eqfBadge(g.eqf.typical)}</strong><span>tipikus munkaköri szint</span></div></div></div>
    <div class="legend"><span><span class="sw" style="background:var(--ink)"></span>saját szint</span><span><span class="sw" style="background:var(--accent)"></span>elvárt szint</span><span><span class="sw" style="background:var(--surface);box-shadow:inset 0 0 0 1.5px var(--muted)"></span>nincs meg</span><span>a vonal a hiányzó szinteket jelöli</span></div>
    <div class="scroll-x"><table><thead><tr><th>Kompetencia</th><th>Fontosság</th><th>EQF: saját → elvárt</th><th>Állapot</th></tr></thead><tbody>${rows}</tbody></table></div>
    ${all.length > 25 ? `<button type="button" class="link more" data-more="${esc(key)}">${state.showAll[key] ? 'Csak az első 25' : `Mind a ${all.length} kompetencia`}</button>` : ''}</div>`;
}

// ------------------------------------------------------------------ 5. kompetenciatér
/* A szerver adja az MDS-koordinátákat (0–1) és a klasztereket; itt: átfedésmentesítés, burkok, rajz, nagyítás. */
const SW = 1000, SH = 680, SPAD = 70;

async function runSpace() {
  const stage = $('space-stage');
  const hasPerson = activeComps().length > 0;
  if (!state.jobs.length && !hasPerson) {
    stage.innerHTML = '<div class="empty"><h2>Nincs mit megjeleníteni</h2><p>Válassz munkakört (1. vagy 3. fül), és/vagy készíts egyéni profilt (2. fül).</p></div>';
    $('space-clusters').textContent = '–'; return;
  }
  if (!state.space) {
    setStatus('space-status', 'A tér számítása…');
    try {
      state.space = await api('lab/space', { job_ids: state.jobs.map((j) => j.id), person: hasPerson ? personPayload() : null });
      relax(state.space);
      state.spaceView = { x: 0, y: 0, k: 1 };
      setStatus('space-status', '');
    } catch (e) { setStatus('space-status', e.message, true); return; }
  }
  const sp = state.space, opts = [...sp.jobs.map((j) => [j.job_id, j.title + (j.fit != null ? ` · ${pct(j.fit)}` : '')])];
  if (!sp.jobs.some((j) => j.job_id === state.spaceFocus)) state.spaceFocus = sp.jobs.some((j) => j.job_id === state.active) ? state.active : sp.jobs[0]?.job_id || '';
  $('space-focus').innerHTML = opts.map(([v, t]) => `<option value="${esc(v)}" ${v === state.spaceFocus ? 'selected' : ''}>${esc(t)}</option>`).join('') + `<option value="" ${!state.spaceFocus ? 'selected' : ''}>${hasPerson ? 'csak az egyéni profil' : 'munkakör nélkül'}</option>`;
  drawSpace();
}
$('space-focus').onchange = (e) => { state.spaceFocus = e.target.value; drawSpace(); };
$('space-edges').onchange = $('space-labels').onchange = () => drawSpace();

function nodeR(n) { return 4.5 + 10 * (n.importance || (n.own ? 0.25 : 0)); }
function relax(sp) {
  // képernyőkoordináták + determinisztikus átfedésmentesítés (a sorrend és a lépésszám rögzített)
  const W = SW - 2 * SPAD, H = SH - 2 * SPAD;
  for (const n of sp.nodes) { n.sx = SPAD + n.x * W; n.sy = SPAD + (1 - n.y) * H; }
  for (let it = 0; it < 120; it++) {
    let moved = false;
    for (let i = 0; i < sp.nodes.length; i++) for (let j = i + 1; j < sp.nodes.length; j++) {
      const a = sp.nodes[i], b = sp.nodes[j], min = nodeR(a) + nodeR(b) + 5;
      let dx = b.sx - a.sx, dy = b.sy - a.sy, d = Math.hypot(dx, dy);
      if (d >= min) continue;
      if (d < 0.01) { dx = ((i * 7 + j * 3) % 11) - 5 || 1; dy = ((i * 5 + j) % 9) - 4 || 1; d = Math.hypot(dx, dy); }
      const push = (min - d) / 2, ux = dx / d, uy = dy / d;
      a.sx -= ux * push; a.sy -= uy * push; b.sx += ux * push; b.sy += uy * push; moved = true;
    }
    if (!moved) break;
  }
  for (const n of sp.nodes) { n.sx = Math.max(24, Math.min(SW - 24, n.sx)); n.sy = Math.max(24, Math.min(SH - 24, n.sy)); }
  const at = (p) => ({ sx: SPAD + p.x * W, sy: SPAD + (1 - p.y) * H });
  for (const j of sp.jobs) Object.assign(j, at(j));
  if (sp.person) Object.assign(sp.person, at(sp.person));
}

function hull(points) {
  // konvex burok (monoton lánc)
  const p = [...points].sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  if (p.length < 3) return p;
  const cross = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const lower = [], upper = [];
  for (const q of p) { while (lower.length >= 2 && cross(lower.at(-2), lower.at(-1), q) <= 0) lower.pop(); lower.push(q); }
  for (const q of p.reverse()) { while (upper.length >= 2 && cross(upper.at(-2), upper.at(-1), q) <= 0) upper.pop(); upper.push(q); }
  return lower.slice(0, -1).concat(upper.slice(0, -1));
}

function nodeState(n, focus) {
  // ok / under / missing a nézőpont munkakörénél; extra: csak az egyéné; other: más kiválasztott munkakör igényli
  if (focus && n.jobs[focus]) return n.jobs[focus].status || 'req';
  if (n.own) return 'extra';
  return 'other';
}
const NODE_STYLE = {
  ok: ['var(--good)', '✓', 'megvan'], under: ['var(--warn)', '▽', 'szint alatt'], missing: ['var(--crit)', '✕', 'hiányzik'],
  req: ['var(--accent)', '', 'a munkakör igényli'], extra: ['var(--surface)', '', 'többlet: csak a te profilodban'],
  other: ['var(--dot-off)', '', 'más kiválasztott munkakör igényli'],
};

function drawSpace() {
  const sp = state.space; if (!sp) return;
  const focus = state.spaceFocus, showEdges = $('space-edges').checked, allLabels = $('space-labels').checked;
  const byId = Object.fromEntries(sp.nodes.map((n) => [n.id, n]));
  const v = state.spaceView;
  let s = `<svg id="space-svg" viewBox="0 0 ${SW} ${SH}" width="100%" role="img" aria-label="Kompetenciák tere: klaszterek és illeszkedés" style="touch-action:none;cursor:grab"><g transform="translate(${v.x} ${v.y}) scale(${v.k})">`;
  // klaszterburkok: vastag, lekerekített körvonal ugyanazzal a kitöltéssel = sima „buborék”
  for (const c of sp.clusters) {
    const pts = sp.nodes.filter((n) => n.cluster === c.id).map((n) => [n.sx, n.sy]);
    if (!pts.length) continue;
    const h = hull(pts), d = h.length === 1 ? `M${h[0][0]} ${h[0][1]}h0.1` : 'M' + h.map((q) => q.join(' ')).join('L') + 'Z';
    s += `<path d="${d}" class="hull" data-cl="${c.id}" stroke-width="54" stroke-linejoin="round" stroke-linecap="round"/>`;
  }
  if (showEdges) for (const e of sp.edges) {
    const a = byId[e.a], b = byId[e.b];
    s += `<line class="sedge" x1="${a.sx}" y1="${a.sy}" x2="${b.sx}" y2="${b.sy}" stroke-width="${0.6 + e.w * 1.6}"/>`;
  }
  // távolságvonalak: egyén → munkakörök (a nézőpont kiemelve)
  if (sp.person) for (const j of sp.jobs) {
    const on = j.job_id === focus;
    s += `<line class="${on ? 'dist-on' : 'dist-off'}" x1="${sp.person.sx}" y1="${sp.person.sy}" x2="${j.sx}" y2="${j.sy}"/>`;
  }
  // csomópontok
  for (const n of sp.nodes) {
    const st = nodeState(n, focus), [fill, glyph, txt] = NODE_STYLE[st], r = nodeR(n), j = focus && n.jobs[focus];
    const tip = `<b>${esc(n.label)}</b>${esc(n.group_label)}<br>${esc(txt)}${j ? ` · elvárt EQF ${j.required_eqf ?? '–'}` : ''}${n.own ? ` · saját ${n.own_eqf ?? 'szint nélkül'}` : ''}${j ? `<br>fontosság ${pct(j.importance)}` : ''}<br><span style="color:var(--muted)">${esc(sp.clusters[n.cluster]?.label || '')}</span>`;
    s += `<g class="snode" data-node="${esc(n.id)}" data-cl="${n.cluster}" data-tip="${esc(tip)}" tabindex="0">
      <circle cx="${n.sx}" cy="${n.sy}" r="${r + 6}" class="hit"/>
      <circle cx="${n.sx}" cy="${n.sy}" r="${r}" fill="${fill}" class="n-${st}"/>
      ${glyph && r >= 8 ? `<text x="${n.sx}" y="${n.sy + 3.5}" text-anchor="middle" class="nglyph ${st === 'under' ? 'dark' : ''}">${glyph}</text>` : ''}</g>`;
  }
  // címkék: fontossági sorrendben, ütközéselkerüléssel (a horgonypontoké elöl)
  const short = (t) => (t.length > 30 ? t.slice(0, 29) + '…' : t);
  const items = [];
  for (const j of sp.jobs) items.push({ x: j.sx, y: j.sy, r: 9, text: j.title + (j.fit != null ? ' · ' + pct(j.fit) : ''), cls: 'alabel' + (j.job_id === focus ? ' on' : '') });
  if (sp.person) items.push({ x: sp.person.sx, y: sp.person.sy, r: 11, text: 'te', cls: 'alabel me' });
  // klaszternevek: a burok fölé, ha ott nincs hely, alá (a keretbe szorítva)
  for (const c of sp.clusters) {
    const m = sp.nodes.filter((n) => n.cluster === c.id); if (!m.length) continue;
    const cx = m.reduce((a, n) => a + n.sx, 0) / m.length, top = Math.min(...m.map((n) => n.sy)), bottom = Math.max(...m.map((n) => n.sy));
    const text = c.label.length > 42 ? c.label.slice(0, 41) + '…' : c.label, w = text.length * 7.4 + 4;
    const clamp = (v) => Math.max(8, Math.min(SW - 8 - w, v)), x = clamp(cx - w / 2);
    const tries = [];
    for (const dx of [0, -0.4 * w, 0.4 * w, -0.8 * w, 0.8 * w]) tries.push([clamp(x + dx), top - 44, 'start'], [clamp(x + dx), bottom + 30, 'start']);
    tries.push([x, top - 60, 'start'], [x, bottom + 46, 'start']);
    items.push({ text, cls: 'cllabel', w, tries });
  }
  const ranked = [...sp.nodes].sort((a, b) => (b.importance + (nodeState(b, focus) === 'missing' ? .3 : 0)) - (a.importance + (nodeState(a, focus) === 'missing' ? .3 : 0)));
  for (const n of allLabels ? ranked : ranked.slice(0, 18)) items.push({ x: n.sx, y: n.sy, r: nodeR(n), text: short(n.label), cls: 'nlabel' });
  s += placeLabels(items, sp.nodes);
  // horgonypontok: munkakörök (négyzet) és az egyén (gyűrű)
  for (const j of sp.jobs) {
    const on = j.job_id === focus;
    s += `<g class="anchor ${on ? 'on' : ''}" data-tip="<b>${esc(j.title)}</b>a munkakör kompetenciáinak súlypontja${j.fit != null ? `<br>illeszkedés ${pct(j.fit)}` : ''} · tipikus EQF ${j.eqf ?? '–'}" data-focus="${esc(j.job_id)}" tabindex="0">
      <rect x="${j.sx - 8}" y="${j.sy - 8}" width="16" height="16" rx="3"/></g>`;
  }
  if (sp.person) s += `<g class="anchor me" data-tip="<b>Te</b>a profilod kompetenciáinak súlypontja"><circle cx="${sp.person.sx}" cy="${sp.person.sy}" r="10"/><circle cx="${sp.person.sx}" cy="${sp.person.sy}" r="4"/></g>`;
  s += '</g></svg>';

  const legend = (focus ? ['ok', 'under', 'missing'] : sp.person ? [] : ['req']).concat(sp.person ? ['extra'] : [], sp.jobs.length > 1 || (!focus && sp.jobs.length) ? ['other'] : [])
    .map((k) => `<span><svg width="14" height="14" style="display:inline;vertical-align:-2px"><circle cx="7" cy="7" r="6" fill="${NODE_STYLE[k][0]}" class="n-${k}"/></svg> ${NODE_STYLE[k][1] ? NODE_STYLE[k][1] + ' ' : ''}${esc(NODE_STYLE[k][2])}</span>`).join('');
  const rows = [...sp.nodes].sort((a, b) => a.cluster - b.cluster || b.importance - a.importance).map((n) => {
    const st = nodeState(n, focus), j = focus && n.jobs[focus];
    return `<tr><td>${esc(n.label)}</td><td class="small">${esc(sp.clusters[n.cluster]?.label || '')}</td><td>${['ok', 'under', 'missing'].includes(st) ? stPill(st) : `<span class="small muted">${esc(NODE_STYLE[st][2])}</span>`}</td><td class="num">${j?.required_eqf ?? '–'}</td><td class="num">${n.own ? (n.own_eqf ?? '✓') : '–'}</td></tr>`;
  }).join('');
  $('space-stage').innerHTML = `<div class="card"><div class="card-head"><div><span class="eyebrow">KOMPETENCIATÉR</span><h2>${focus ? esc(sp.jobs.find((j) => j.job_id === focus)?.title || '') + ' — illeszkedés a térben' : 'A kompetenciák tere'}</h2>
      <p class="small muted" style="margin:4px 0 0">${sp.nodes.length} kompetencia · ${sp.clusters.length} klaszter · a közelség az együttes előfordulást jelzi ${sp.method.tesz_count} TESZ-SZÍN alapján</p></div>
      <div class="row-buttons" style="margin:0"><button type="button" class="secondary" id="space-reset">Nézet visszaállítása</button></div></div>
    <div class="legend">${legend}<span><svg width="14" height="14" style="display:inline;vertical-align:-2px"><rect x="1" y="1" width="12" height="12" rx="3" class="anchor-sw"/></svg> munkakör</span>${sp.person ? '<span><svg width="14" height="14" style="display:inline;vertical-align:-2px"><circle cx="7" cy="7" r="5.5" class="me-sw"/></svg> te</span>' : ''}<span class="muted">nagyobb kör = fontosabb</span></div>
    <div class="space-wrap">${s}</div>
    <p class="muted small">Görgetéssel nagyítás, húzással mozgatás · egy munkakör négyzetére kattintva az lesz a nézőpont · a klaszter fölé állva kiemelődik.</p>
    <details class="io"><summary>Táblázatos nézet (${sp.nodes.length} kompetencia)</summary><div class="scroll-x"><table><thead><tr><th>Kompetencia</th><th>Klaszter</th><th>Állapot</th><th>Elvárt EQF</th><th>Saját</th></tr></thead><tbody>${rows}</tbody></table></div></details></div>`;
  renderClusters(sp, focus);
  wireSpace();
}

function placeLabels(items, nodes) {
  // mohó elhelyezés: jobbra, balra, fölé, alá; ami sehová nem fér, kimarad (a tooltip és a táblázat mutatja)
  const boxes = nodes.map((n) => { const r = nodeR(n); return [n.sx - r, n.sy - r, n.sx + r, n.sy + r]; });
  const hit = (b) => b[0] < 4 || b[2] > SW - 4 || b[1] < 4 || b[3] > SH - 4 || boxes.some((o) => b[0] < o[2] && b[2] > o[0] && b[1] < o[3] && b[3] > o[1]);
  let out = '';
  for (const it of items) {
    const w = it.w || it.text.length * (it.cls.startsWith('alabel') ? 6.6 : 6.1) + 4, h = 13, g = (it.r || 0) + 4;
    const tries = it.tries || [[it.x + g, it.y - h / 2, 'start'], [it.x - g - w, it.y - h / 2, 'end'], [it.x - w / 2, it.y - g - h, 'middle'], [it.x - w / 2, it.y + g, 'middle']];
    for (const [bx, by, anchor] of tries) {
      const b = [bx, by, bx + w, by + h];
      // a saját csomópont dobozát nem számítjuk ütközésnek
      const own = it.x == null ? -1 : boxes.findIndex((o) => Math.abs((o[0] + o[2]) / 2 - it.x) < 0.5 && Math.abs((o[1] + o[3]) / 2 - it.y) < 0.5);
      const saved = own >= 0 ? boxes.splice(own, 1)[0] : null;
      const bad = hit(b);
      if (saved) boxes.splice(own, 0, saved);
      if (bad) continue;
      boxes.push(b);
      const tx = anchor === 'start' ? bx : anchor === 'end' ? bx + w : bx + w / 2;
      out += `<text class="${it.cls}" x="${tx}" y="${by + 10}" text-anchor="${anchor}">${esc(it.text)}</text>`;
      break;
    }
  }
  return out;
}

function renderClusters(sp, focus) {
  $('space-clusters').innerHTML = sp.clusters.map((c) => {
    const j = focus && c.jobs[focus];
    return `<div class="cl" data-cl="${c.id}" tabindex="0"><div class="cl-head"><b>${esc(c.label)}</b><span class="muted">${c.size}</span></div>
      ${j ? `<div class="fitcell">${bar(j.fit, 'thin')}<b class="small">${pct(j.fit)}</b></div><span class="muted">${j.required} igényelt · ${j.missing ? `✕ ${j.missing} hiányzik` : '✓ nincs hiány'}${j.under ? ` · ▽ ${j.under} szint alatt` : ''}</span>`
        : `<span class="muted">${focus ? 'a munkakör nem igényli' : ''}${c.own ? `${focus ? ' · ' : ''}${c.own} saját kompetencia` : ''}</span>`}
      <div class="muted" style="margin-top:2px">${c.top.map(esc).join(', ')}</div></div>`;
  }).join('');
  $('space-clusters').querySelectorAll('.cl').forEach((el) => {
    el.onmouseenter = el.onfocus = () => highlightCluster(el.dataset.cl);
    el.onmouseleave = el.onblur = () => highlightCluster(null);
  });
}
function highlightCluster(id) {
  const svg = $('space-svg'); if (!svg) return;
  svg.classList.toggle('focus-cl', id != null);
  svg.querySelectorAll('[data-cl]').forEach((e) => e.classList.toggle('hl', id != null && e.dataset.cl === String(id)));
}

function wireSpace() {
  const svg = $('space-svg'), g = svg.querySelector('g'), v = state.spaceView;
  const apply = () => g.setAttribute('transform', `translate(${v.x} ${v.y}) scale(${v.k})`);
  svg.addEventListener('wheel', (e) => {
    e.preventDefault();
    const r = svg.getBoundingClientRect(), mx = (e.clientX - r.left) * SW / r.width, my = (e.clientY - r.top) * SH / r.height;
    const k2 = Math.min(5, Math.max(0.5, v.k * (e.deltaY > 0 ? 0.9 : 1.1)));
    v.x = mx - (mx - v.x) * k2 / v.k; v.y = my - (my - v.y) * k2 / v.k; v.k = k2; apply();
  }, { passive: false });
  let drag = null;
  svg.onpointerdown = (e) => { if (e.target.closest('.anchor')) return; drag = { x: e.clientX, y: e.clientY, vx: v.x, vy: v.y }; svg.setPointerCapture(e.pointerId); svg.style.cursor = 'grabbing'; };
  svg.onpointermove = (e) => { if (!drag) return; const r = svg.getBoundingClientRect(); v.x = drag.vx + (e.clientX - drag.x) * SW / r.width; v.y = drag.vy + (e.clientY - drag.y) * SH / r.height; apply(); };
  svg.onpointerup = svg.onpointercancel = () => { drag = null; svg.style.cursor = 'grab'; };
  svg.querySelectorAll('[data-focus]').forEach((a) => (a.onclick = a.onkeydown = (e) => {
    if (e.type === 'keydown' && e.key !== 'Enter') return;
    state.spaceFocus = a.dataset.focus; $('space-focus').value = state.spaceFocus; drawSpace();
  }));
  svg.querySelectorAll('.hull').forEach((h) => { h.onmouseenter = () => highlightCluster(h.dataset.cl); h.onmouseleave = () => highlightCluster(null); });
  $('space-reset').onclick = () => { state.spaceView = { x: 0, y: 0, k: 1 }; drawSpace(); };
}

// ------------------------------------------------------------------ módszertan (a panelekben)
const METHOD = `<ul>
  <li><b>TESZ-SZÍN súlya</b> a munkakörben: relevancia (alap 1, támogató 0,5) × gyakoriság (naponta 1, hetente 0,85, havonta 0,7, ritkán 0,5).</li>
  <li><b>Munkakör EQF-szintje</b>: a TESZ-SZÍN-ek elvárt EQF-értékének súlyozott mediánja.</li>
  <li><b>Kompetencia fontossága</b>: az őt igénylő TESZ-SZÍN-ek súlyösszege; <b>elvárt szintje</b>: ezek közül az alap TESZ-SZÍN-ek legmagasabb EQF-je.</li>
  <li><b>Hiány</b> kompetenciánként: (elvárt − saját) / elvárt, 0 és 1 között; hiányzó kompetenciánál 1. Szint nélküli kompetenciánál az általános EQF-szinted számít.</li>
  <li><b>Távolság</b> = a hiányok fontossággal súlyozott átlaga; <b>illeszkedés</b> = 1 − távolság. <b>Lefedettség</b>: a súlyozott kompetenciák megléte, szinttől függetlenül.</li></ul>
  <p>Az egyéni EQF-szintek AI-becslések a dokumentum alapján; a munkaköri szintek a felbontás (P1) szakmai becslései.</p>`;
$('method-rec').innerHTML = METHOD + '<p>Sávok: kész illeszkedés ≥ 85%, közeli 60–85%, fejlesztendő &lt; 60%.</p>';
$('method-gap').innerHTML = METHOD;
$('method-space').innerHTML = `<ul>
  <li><b>Hasonlóság</b>: két kompetencia hányszor szerepel együtt ugyanannak a TESZ-SZÍN-nek az igényei között, az összes felbontott munkakörön: n(a,b) / √(n(a)·n(b)).</li>
  <li><b>Távolság</b>: a 6 legközelebbi szomszéd hálózatán vett legrövidebb út (élsúly 1 − hasonlóság), így a lánc mentén összefüggő kompetenciák is közel kerülnek.</li>
  <li><b>Elrendezés</b>: klasszikus többdimenziós skálázás (MDS) két dimenzióba; utána csak az átfedéseket toljuk szét. A tengelyeknek nincs önálló jelentése, csak a távolságoknak.</li>
  <li><b>Klaszterek</b>: átlagos kapcsolású hierarchikus csoportosítás ugyanezen a távolságon; a név a tagok leggyakoribb KÉSZ-csoportja.</li>
  <li><b>Horgonypontok</b>: a munkakör a kompetenciáinak fontossággal súlyozott súlypontjában, az egyén a saját kompetenciái súlypontjában áll.</li>
  <li>A klaszterenkénti sáv a nézőpont munkakörének illeszkedése az adott klaszterben (ugyanaz a képlet, mint a 4. fülön).</li></ul>`;

// ------------------------------------------------------------------ téma, indulás
(function theme() {
  const root = document.documentElement;
  try { const t = localStorage.getItem('gh-theme'); if (t) root.dataset.theme = t; } catch { /* */ }
  $('theme').onclick = () => {
    const dark = root.dataset.theme ? root.dataset.theme === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
    root.dataset.theme = dark ? 'light' : 'dark';
    try { localStorage.setItem('gh-theme', root.dataset.theme); } catch { /* */ }
  };
})();

restore();
renderContext();
requireUser().then(async () => {
  api('config').then((c) => { $('active-model').textContent = 'AI: ' + (c.default_model ? c.default_provider + ' / ' + c.default_model : 'nincs beállítva'); }).catch(() => {});
  const comps = await api('lab/competencies');
  state.catalog = Object.fromEntries(comps.items.map((c) => [c.id, c]));
  await loadJobs();
  // a tárolt munkakörök közül a már nem felbontottakat elhagyjuk
  const known = await api('lab/jobs');
  const ids = new Set(known.items.map((j) => j.id));
  state.jobs = state.jobs.filter((j) => ids.has(j.id));
  renderContext(); renderSkillList(); renderPerson(); renderJobStage();
  const h = location.hash.slice(1);
  setTab(TABS.includes(h) ? h : 'job');
}).catch((e) => setStatus('job-status', e.message, true));
