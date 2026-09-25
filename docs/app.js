// Every tracker runs this same dashboard; the page that loads it declares
// what it is looking at. See docs/index.html for the shape of TRACKER.
const T = window.TRACKER;
const PATH = T.path;                 // path inside the repo, for saving back
const DATA = T.data;                 // path the browser fetches
const DEFAULT_ROLE = T.defaultRole;  // role bucket for "no discipline named"
let data = null;

const $ = id => document.getElementById(id);

// ---- your marks live in this browser first ------------------------------
// Applied/Skip used to exist only inside the tab: the buttons wrote into
// `data` and a copy in `dirty`, and both died with the page. Anything marked
// before a GitHub token was set - or while a save was failing - was gone on
// the next refresh. Even a save that worked could look like forgetting,
// because Pages republishes the JSON a minute or two later and the poll in
// between pulled the pre-save copy back over the marks.
//
// So a click now lands in localStorage immediately, is re-applied over every
// copy of the data we fetch, and is only forgotten once the fetched file
// itself carries it. Writing back to GitHub became a sync step on top of
// that, instead of the only place the mark exists.
//
// Keyed by the tracker's own path, so the two trackers never share marks.
const MARKS_KEY = "marks:" + PATH;
const MARK_TTL_DAYS = 30;   // for a mark whose posting has left the feed
let marks = readMarks();

function readMarks() {
  try {
    const raw = JSON.parse(localStorage.getItem(MARKS_KEY) || "{}");
    const out = {};
    // ignore anything that is not shaped like a mark, so a half-written or
    // hand-edited key can never take the dashboard down at boot
    for (const [id, m] of Object.entries(raw))
      if (m && typeof m.status === "string")
        out[id] = { status: m.status, applied_on: m.applied_on || null,
                    ts: +m.ts || Date.now(), synced: +m.synced || 0 };
    return out;
  } catch (e) { return {}; }
}

let storageWarned = false;
function writeMarks() {
  try {
    localStorage.setItem(MARKS_KEY, JSON.stringify(marks));
  } catch (e) {
    // private mode or a full quota: the marks still work for this tab
    if (!storageWarned) { storageWarned = true; toast("This browser will not store marks: " + e.message); }
  }
}

// Overlay the marks onto a freshly fetched copy, and drop the ones that copy
// has caught up with - that round trip, not the PUT's status code, is what
// proves a mark is safely in the repo.
function applyMarks(target) {
  let changed = false;
  for (const [id, m] of Object.entries(marks)) {
    const j = target.jobs[id];
    if (!j) {
      // the posting is no longer tracked (pruned, or re-keyed): nothing left
      // to apply the mark to, so let it go rather than keep it forever
      if (Date.now() - m.ts > MARK_TTL_DAYS * 86400000) { delete marks[id]; changed = true; }
      continue;
    }
    if (j.status === m.status && (j.applied_on || null) === m.applied_on) {
      delete marks[id]; changed = true;      // confirmed by the file itself
      continue;
    }
    j.status = m.status;
    if (m.applied_on) j.applied_on = m.applied_on; else delete j.applied_on;
  }
  if (changed) writeMarks();
}

const unsynced = () => Object.values(marks).filter(m => !m.synced).length;
const cfg = () => ({
  owner: localStorage.gh_owner || "", repo: localStorage.gh_repo || "",
  branch: localStorage.gh_branch || "main", token: localStorage.gh_token || "",
});

async function load() {
  let r;
  try {
    r = await fetch(DATA + "?" + Date.now());
    if (!r.ok) throw new Error("HTTP " + r.status);
    data = await r.json();
    applyMarks(data);
  } catch (e) {
    $("list").innerHTML = "<p style='color:var(--muted)'>Could not load " + esc(DATA) +
      " (" + esc(e.message) + "). Run a scan workflow first, then refresh.</p>";
    return;
  }
  await loadH1b();
  fillCompanies();
  fillRoles();
  fillSources();
  fillH1b();
  render();
  startAutoRefresh();
  // marks left over from a previous visit (token missing then, save failed,
  // tab closed inside the debounce) get one more try now
  if (unsynced() && cfg().token) scheduleSave();
}

const ROLE_LABEL = T.roles;

// ---- H-1B sponsorship ---------------------------------------------------
// Built by its own workflow into data/h1b.json, keyed by the same company
// string every posting already carries - which is what makes the signal cover
// the whole backlog the moment the file lands, with no re-scan and no
// migration of jobs.json.
//
// Three states, and they are not the same thing:
//   a record   this employer has filed, and here is how much
//   null       we looked it up and found nothing. NOT "does not sponsor" -
//              Northrop Grumman has no records at all, which is a hole in the
//              disclosure data rather than a fact about the employer
//   undefined  not looked up (file missing, or a company added since the last
//              refresh). Shows nothing at all.
let h1b = {};

async function loadH1b() {
  try {
    const r = await fetch("data/h1b.json?" + Date.now());
    if (!r.ok) throw new Error("HTTP " + r.status);
    h1b = (await r.json()).companies || {};
  } catch (e) {
    h1b = {};   // never fatal: no badges beats no dashboard
  }
}

// undefined (not looked up) and null (looked up, nothing found) are different
// answers, so this returns the raw value rather than coercing either away.
const h1bOf = j => h1b[j.company];

function fillH1b() {
  const sel = $("fH1b");
  if (!sel) return;
  const keep = sel.value;
  let yes = 0, none = 0, staffing = 0;
  for (const j of Object.values(data.jobs)) {
    const h = h1bOf(j);
    if (h) { yes++; if (h.staffing) staffing++; }
    else if (h === null) none++;
  }
  sel.innerHTML =
    '<option value="">Any sponsorship</option>' +
    `<option value="yes">Sponsors H-1B (${yes.toLocaleString()})</option>` +
    `<option value="none">No filings found (${none.toLocaleString()})</option>` +
    `<option value="staffing">Staffing agency (${staffing.toLocaleString()})</option>`;
  sel.value = keep;   // survive a refresh
}

function h1bMatches(j, want) {
  if (!want) return true;
  const h = h1bOf(j);
  if (want === "yes") return !!h;
  if (want === "staffing") return !!h && h.staffing;
  return h === null;            // "none" is the looked-up-and-absent case only
}

// LinkedIn arrives through the jobspy fetcher and is the one source that is
// a search rather than a listing: it reaches employers no registry covers,
// but it samples them, and its rows carry the board's URL rather than the
// employer's. Worth being able to isolate, or to set aside.
const LINKEDIN_SOURCE = "jobspy-linkedin";
const isLinkedIn = j => (j.source || "") === LINKEDIN_SOURCE;

function fillSources() {
  const sel = $("fSource");
  if (!sel) return;
  const keep = sel.value;
  let li = 0, rest = 0;
  for (const j of Object.values(data.jobs)) (isLinkedIn(j) ? li++ : rest++);
  sel.innerHTML =
    '<option value="">Any source</option>' +
    `<option value="linkedin">LinkedIn only (${li.toLocaleString()})</option>` +
    `<option value="direct">Excluding LinkedIn (${rest.toLocaleString()})</option>`;
  sel.value = keep;   // survive a refresh
}

function fillRoles() {
  const keep = $("fRole").value;
  const counts = {};
  for (const j of Object.values(data.jobs)) {
    const r = j.role || DEFAULT_ROLE;
    counts[r] = (counts[r] || 0) + 1;
  }
  const order = Object.keys(counts).sort((a, b) => counts[b] - counts[a]);
  $("fRole").innerHTML = '<option value="">All roles</option>' +
    order.map(r => `<option value="${r}">${ROLE_LABEL[r] || r} (${counts[r]})</option>`).join("");
  if (order.includes(keep)) $("fRole").value = keep;
}

function fillCompanies() {
  const keep = $("fCompany").value;
  const companies = [...new Set(Object.values(data.jobs).map(j => j.company))].sort();
  $("fCompany").innerHTML = '<option value="">All companies</option>' +
    companies.map(c => `<option>${esc(c)}</option>`).join("");
  if (companies.includes(keep)) $("fCompany").value = keep;   // survive a refresh
}

// ---- live updates -------------------------------------------------------
// A scan commits jobs.json every hour and Pages redeploys it, so an open tab
// goes stale. Poll cheaply with HEAD and only pull the ~500KB body when the
// file has actually changed.
let lastTag = null, polling = false;

async function checkForUpdates(force = false) {
  if (polling) return;
  polling = true;
  try {
    let tag = null;
    try {
      const h = await fetch(DATA, { method: "HEAD", cache: "no-store" });
      tag = h.headers.get("etag") || h.headers.get("last-modified");
    } catch (e) { /* HEAD unsupported or offline - fall through to a full read */ }
    if (!force && tag && tag === lastTag) { touchAgo(); return; }
    const r = await fetch(DATA + "?t=" + Date.now(), { cache: "no-store" });
    if (!r.ok) return;
    const fresh = await r.json();
    if (!force && data && fresh.updated === data.updated) { lastTag = tag; touchAgo(); return; }
    const before = data ? Object.keys(data.jobs).length : 0;
    // re-apply your marks, so neither a scan landing mid-edit nor a Pages
    // deploy still serving the pre-save file can undo them on screen
    applyMarks(fresh);
    data = fresh;
    lastTag = tag;
    fillCompanies();
    fillRoles();
    fillSources();
    fillH1b();   // a scan adds companies, which moves the sponsorship counts
    render();
    const added = Object.keys(data.jobs).length - before;
    if (added > 0) toast(`${added} new role${added === 1 ? "" : "s"} from the latest scan`);
  } catch (e) { /* transient - the next tick retries */ }
  finally { polling = false; }
}

function touchAgo() {   // keep "last scan Xm ago" honest between refreshes
  if (data) $("heroSub").textContent = `last scan ${ago(data.updated)}`;
}

function startAutoRefresh() {
  setInterval(() => { if (!document.hidden) checkForUpdates(); }, 60000);
  // coming back to the tab should show current data straight away
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) checkForUpdates();
  });
}

const TIERS = T.tiers.map(t => [t[0], t[1]]);
const STATUS_WORD = { open:"open", applied:"applied", skip:"skipped", interview:"in interview", "":"tracked" };

// ---- duplicate requisitions ---------------------------------------------
// Big employers post one role as many separate reqs: 22 "Software Engineer III"
// in Bentonville, 16 "Lead Software Engineer, Full Stack" in McLean. Each is a
// real requisition with its own id and its own apply link, so the scanner is
// right to keep them apart - it is the *view* that drowns, one company's hiring
// push crowding everything else off the screen. They fold into a single row
// here, and the "N openings" badge opens the full list: nothing is hidden, and
// nothing about the stored data changes.
const groupKey = j => [j.company, j.title, j.location]
  .map(s => (s || "").replace(/\s+/g, " ").trim().toLowerCase()).join("\u0000");

// Short printable token, so a group can be named in a data attribute without
// carrying a company name's punctuation into the markup. Groups are keyed by
// the full string and never by the token, so a collision could only ever open
// two rows at once - it can never fold two different roles together.
function keyToken(s) {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 0x01000193); }
  return (h >>> 0).toString(36);
}

const grouping = () => !$("fGroup") || $("fGroup").value === "1";
const expanded = new Set();   // group tokens the reader has opened

function groupRows(rows, cmp) {
  const on = grouping();
  const by = new Map();
  for (const row of rows) {
    // with grouping off every posting is its own group of one, so a single
    // code path renders both modes
    const k = on ? groupKey(row[1]) : row[0];
    const g = by.get(k);
    if (g) g.members.push(row);
    else by.set(k, { token: keyToken(k), members: [row] });
  }
  const out = [];
  for (const g of by.values()) {
    // the representative is whichever member the active sort puts first, so a
    // folded row answers "newest posted" - or any other sort - honestly, and
    // the date on its face is the freshest of the reqs it stands for
    if (g.members.length > 1) g.members.sort(cmp);
    g.rep = g.members[0];
    out.push(g);
  }
  return out;
}

function comparator(mode) {
  return (a, b) => {
    if (mode === "comp" && (!!a[1].comp !== !!b[1].comp)) return a[1].comp ? -1 : 1;
    if (mode === "yoe") {
      // postings that state no bar sort last rather than pretending to be 0
      const ya = a[1].yoe == null ? 99 : a[1].yoe, yb = b[1].yoe == null ? 99 : b[1].yoe;
      if (ya !== yb) return ya - yb;
    }
    if (mode === "posted") {
      // fall back to first_seen so entries with no posted_at still order sanely
      const pa = a[1].posted_at || a[1].first_seen || "";
      const pb = b[1].posted_at || b[1].first_seen || "";
      if (pa !== pb) return pb.localeCompare(pa);
    }
    return (b[1].first_seen || "").localeCompare(a[1].first_seen || "");
  };
}

function render() {
  const tier = $("fTier").value, status = $("fStatus").value,
        comp = $("fCompany").value, q = $("fSearch").value.toLowerCase(),
        role = $("fRole").value, yoe = $("fYoe").value,
        src = $("fSource") ? $("fSource").value : "",
        spon = $("fH1b") ? $("fH1b").value : "";
  // "base" applies every filter EXCEPT tier, so each tile answers
  // "how many would I see if I picked this tier?"
  const base = Object.entries(data.jobs).filter(([id, j]) =>
      (status === "" || (status === "open" ? (j.status === "new") : j.status === status)) &&
      (!comp || j.company === comp) &&
      (!role || (j.role || DEFAULT_ROLE) === role) &&
      (!yoe || (yoe === "unstated" ? j.yoe == null : j.yoe != null && j.yoe <= +yoe)) &&
      (!src || (src === "linkedin") === isLinkedIn(j)) &&
      h1bMatches(j, spon) &&
      (!q || (j.title + " " + j.location + " " + j.company).toLowerCase().includes(q)));
  renderKpi(base, tier, status);
  renderActivity();
  // grouped AFTER filtering, so a row only ever stands for postings you can
  // currently see: skip half a cluster and the badge drops to what is left
  const rows = base.filter(([id, j]) => (!tier || j.tier === tier));
  const cmp = comparator($("fSort") ? $("fSort").value : "posted");
  const groups = groupRows(rows, cmp);
  groups.sort((a, b) => cmp(a.rep, b.rep));

  const folded = rows.length - groups.length;
  $("stats").textContent = `${groups.length.toLocaleString()} shown`
    + (folded ? ` · ${rows.length.toLocaleString()} postings` : "")
    + (tier ? ` · filtered to ${TIERS.find(t=>t[0]===tier)[1]}` : "");

  $("list").innerHTML = groups.map(jobRow).join("")
    || "<p style='color:var(--muted)'>Nothing matches.</p>";
}

function jobRow(g) {
  const [id, j] = g.rep;
  const n = g.members.length;
  const open = n > 1 && expanded.has(g.token);
  const ids = g.members.map(m => m[0]);
  // A cluster is one role to apply to and many reqs to dismiss, so the two
  // buttons cover different ground. Applied/Interview mark the one req this
  // row links to - you applied once, and marking all 22 would log 22
  // applications on the activity heatmap. Skip clears the whole cluster,
  // which is the reason for folding it in the first place.
  const btn = (act, label) =>
    `<button class="${j.status === act ? "active" : ""}" data-act="${act}"
             data-ids="${esc((act === "skip" ? ids : [id]).join(" "))}"${
      n > 1 ? ` title="${act === "skip" ? `Skip all ${n} openings` : `Mark the posting this row links to (1 of ${n})`}"` : ""
    }>${label}</button>`;
  const dupes = n > 1
    ? `<button class="badge dupes${open ? " open" : ""}" data-group="${esc(g.token)}"
               aria-expanded="${open}" title="${esc(
        `${n} separate requisitions for this role at the same location. ` +
        (open ? "Hide them." : "Show them all."))}">${
        open ? "\u25be" : "\u25b8"} ${n} openings</button>`
    : "";
  return `<div class="grp${open ? " open" : ""}">${row(id, j, dupes)}${
    open ? `<div class="members">${g.members.map(m => row(m[0], m[1], "", true)).join("")}</div>` : ""
  }</div>`;

  function row(rid, rj, lead, member = false) {
    const href = safeUrl(rj.url);
    // a posting whose url will not pass as http(s) still shows, just not as a link
    const title = href
      ? `<a class="title" href="${href}" target="_blank" rel="noopener">${esc(rj.title)}</a>`
      : `<span class="title">${esc(rj.title)}</span>`;
    // the ids ride in a data attribute and are read back by one delegated
    // listener, so they never have to survive being parsed as JavaScript
    const mbtn = (act, label) =>
      `<button class="${rj.status === act ? "active" : ""}" data-act="${act}"
               data-ids="${esc(rid)}">${label}</button>`;
    const mk = member ? mbtn : btn;
    // an expanded member repeats none of company, title or location - those are
    // what it was grouped ON, and are already on the row above it - so its meta
    // line carries only what actually tells one req from another
    const meta = member
      ? [rj.posted_at ? "posted " + esc(rj.posted_at) : "first seen " + esc(rj.first_seen),
         esc(rj.source || "")].filter(Boolean).join(" \u00b7 ")
      : `${esc(rj.company)} \u00b7 ${esc(rj.location)} \u00b7 ${
          rj.posted_at ? "posted " + esc(rj.posted_at) : "first seen " + esc(rj.first_seen)}`;
    return `
    <div class="job${member ? " member" : ""} ${rj.status === "applied" ? "applied" : rj.status === "skip" ? "skip" : ""}">
      ${rj.status === "new" ? '<span class="newdot"></span>' : ""}
      <div class="info">
        ${title}
        ${member ? "" : `<span class="pill" style="--tint:var(--t-${esc(rj.tier)})">${esc(rj.tier)}</span>`}
        <div class="meta">${meta}</div>
        ${badges(rj, lead)}
      </div>
      <div class="btns">
        ${mk("applied", "\u2713 Applied")}${mk("skip", "\u2717 Skip")}${mk("interview", "\u2605 Interview")}
      </div>
    </div>`;
  }
}

function ago(iso){
  if (!iso) return "never";
  const mins = Math.floor((Date.now() - Date.parse(iso)) / 60000);
  if (isNaN(mins)) return iso;
  if (mins < 60) return mins <= 1 ? "just now" : mins + "m ago";
  const h = Math.floor(mins / 60);
  return h < 24 ? h + "h ago" : Math.floor(h / 24) + "d ago";
}

function renderKpi(base, tier, status) {
  const total = base.length;
  const word = STATUS_WORD[status] ?? "matching";
  $("heroVal").textContent = total.toLocaleString();
  $("heroLab").textContent = total === 1 ? `${word} role` : `${word} roles`;
  $("heroSub").textContent = `last scan ${ago(data.updated)}`;

  // tier composition of what the hero counts - a 2px surface gap keeps the
  // stacked segments from reading as one continuous bar
  $("heroBar").innerHTML = TIERS.map(([key, label]) => {
    const n = base.filter(([, j]) => j.tier === key).length;
    const pct = total ? n / total * 100 : 0;
    return pct ? `<i style="flex:${pct};background:var(--t-${key})"
                    title="${label}: ${n.toLocaleString()} (${Math.round(pct)}%)"></i>` : "";
  }).join("");

  const all = Object.values(data.jobs);
  $("tiles").innerHTML = TIERS.map(([key, label]) => {
    const n = base.filter(([, j]) => j.tier === key).length;
    const inTier = all.filter(j => j.tier === key);
    const applied = inTier.filter(j => j.status === "applied" || j.status === "interview").length;
    // the meter tracks YOUR progress through this tier, not the tier's size
    const pct = inTier.length ? Math.round(applied / inTier.length * 100) : 0;
    const on = tier === key;
    return `<button class="tile" data-tier="${key}" aria-pressed="${on}"
              style="--tint:var(--t-${key})"
              title="${on ? "Clear the" : "Filter to"} ${label} tier - ${applied} applied of ${inTier.length} tracked">
        <span class="tile-top"><span class="dot"></span>${label}</span>
        <span class="tile-val">${n.toLocaleString()}</span>
        <span class="tile-share"><b style="color:var(--text)">${applied}</b> applied${
          pct ? ` · ${pct}%` : ""}</span>
        <span class="meter" role="img" aria-label="${applied} applied of ${inTier.length} ${label} roles">
          <span class="meter-fill" style="width:${applied ? Math.max(pct, 2) : 0}%"></span></span>
      </button>`;
  }).join("");

  // whole-database standing totals, independent of the filters above
  const count = st => all.filter(j => j.status === st).length;
  const withComp = all.filter(j => j.comp).length;
  // ---- source health, straight from the sources block the scanner writes ----
  const src = data.sources || {};
  const names = Object.keys(src);
  const dead = names.filter(n => (src[n].last || 0) === 0);
  const live = names.length - dead.length;
  const pct = names.length ? Math.round(live / names.length * 100) : 0;
  const cls = pct >= 95 ? "" : pct >= 85 ? " warn" : " bad";
  const detail = dead.length
    ? "Returning nothing:\n" + dead.sort().map(n => {
        const r = src[n];
        return `  • ${n}${r.best ? ` (best ${r.best}` + (r.last_ok ? `, last had jobs ${r.last_ok}` : "") + ")" : ""}`;
      }).join("\n")
    : "Every configured source returned postings on the last scan.";
  const health = `<span class="schip health${cls}" title="${esc(detail)}">` +
    `<span class="hdot"></span>Sources <b>${live}/${names.length}</b></span>`;

  // marks this browser holds that the repo has not acknowledged yet
  const waiting = unsynced();
  const local = waiting ? `<span class="schip local" title="${esc(cfg().token
      ? "Saved in this browser and queued for the repo. They survive a refresh either way."
      : "Saved in this browser only. They survive a refresh, and sync to the repo once you add a GitHub token (⚙).")
    }">${cfg().token ? "Syncing" : "This browser"} <b>${waiting}</b></span>` : "";

  $("statusbar").innerHTML = health + local + [
    ["Open", count("new")], ["Applied", count("applied")],
    ["Interview", count("interview")], ["Skipped", count("skip")],
    ["With pay range", withComp], ["Companies", new Set(all.map(j => j.company)).size],
  ].map(([k, v]) => `<span class="schip">${k} <b>${v.toLocaleString()}</b></span>`).join("");
}

function renderActivity() {
  const days = {};                       // "YYYY-MM-DD" -> applications that day
  for (const j of Object.values(data.jobs))
    if (j.applied_on) days[j.applied_on] = (days[j.applied_on] || 0) + 1;

  const today = new Date(); today.setHours(0, 0, 0, 0);
  const dayMs = 86400000;
  const total = Object.values(days).reduce((a, b) => a + b, 0);

  // current streak: consecutive days with >=1 application. Today not being
  // done yet is not a break, so an empty today falls back to yesterday.
  let cur = 0, probe = new Date(today);
  if (!days[isoDay(probe)]) probe = new Date(today - dayMs);
  while (days[isoDay(probe)]) { cur++; probe = new Date(probe - dayMs); }

  let best = 0, run = 0, prev = null;
  for (const d of Object.keys(days).sort()) {
    run = (prev && (Date.parse(d) - Date.parse(prev)) === dayMs) ? run + 1 : 1;
    best = Math.max(best, run); prev = d;
  }
  const week = [...Array(7)].reduce((a, _, i) => a + (days[isoDay(new Date(today - i * dayMs))] || 0), 0);

  $("stkRow").innerHTML = `${cur}<i>d streak</i>`;
  $("stkRow").classList.toggle("live", cur > 0);
  $("stkBest").textContent = best;
  $("stkWeek").textContent = week;
  $("stkTotal").textContent = total;

  // 30 days, aligned so each column is one Sun-Sat week (5 columns)
  const start = new Date(today - 29 * dayMs);
  start.setDate(start.getDate() - start.getDay());
  const cells = [];
  for (let d = new Date(start); d <= today; d.setDate(d.getDate() + 1)) {
    const iso = isoDay(d), n = days[iso] || 0;
    const lvl = n === 0 ? 0 : n === 1 ? 1 : n === 2 ? 2 : n <= 4 ? 3 : 4;
    cells.push(`<span class="hm-cell${iso === isoDay(today) ? " today" : ""}" data-l="${lvl}"
      title="${n} application${n === 1 ? "" : "s"} on ${iso}"></span>`);
  }
  $("hmGrid").innerHTML = cells.join("");
  $("actEmpty").textContent = total ? "" : "Nothing logged yet";
}

function daysOld(d){
  if (!d) return null;
  const ms = Date.now() - Date.parse(d + "T00:00:00Z");
  return isNaN(ms) ? null : Math.floor(ms / 86400000);
}

// The sponsorship badges say what the filing record says and stop there. No
// red, no "does not sponsor", nothing hidden: a company's filing history is
// evidence about the company, never a ruling on the requisition in front of
// you, and a company missing from the data is missing, not disqualified.
function h1bBadges(j){
  const h = h1bOf(j);
  if (h === undefined) return [];          // never looked up - say nothing
  if (h === null)
    return [`<span class="badge" title="No H-1B filings on record for this employer.
Absence is not proof: some sponsors are simply missing from the disclosure data.">🛂 no H-1B filings found</span>`];

  const out = [];
  // a loose match is one token deep ("Flex" -> "Flex Consulting Group"), so it
  // is marked with a ~ and always names who it matched
  const fuzzy = h.confidence === "loose" || h.confidence === "prefix";
  const since = h.last ? `, most recently ${h.last}` : "";
  const tip = `${h.filed.toLocaleString()} H-1B filings by "${h.matched}"${since}.` +
    (fuzzy ? `\nMatched approximately on the company name - check it is the same employer.`
           : "") +
    `\nCompany history, not a guarantee for this role.`;
  out.push(`<span class="badge h1b" title="${esc(tip)}">🛂 H-1B ${
    fuzzy ? "~" : ""}${h.filed.toLocaleString()}</span>`);
  if (h.staffing)
    out.push(`<span class="badge staffing" title="${esc(
      `"${h.matched}" files as a staffing or consulting agency, so the role is likely to be at a client site.`)
    }">🏢 staffing agency</span>`);
  return out;
}

function badges(j, lead = ""){
  const b = lead ? [lead] : [];
  if (j.comp) b.push(`<span class="badge comp">💰 ${esc(j.comp)}</span>`);
  const age = daysOld(j.posted_at);
  if (age !== null && age <= 3) b.push(`<span class="badge fresh">🔥 ${age <= 0 ? "today" : age + "d ago"}</span>`);
  if (j.workplace) b.push(`<span class="badge${j.workplace === "Remote" ? " remote" : ""}">${esc(j.workplace)}</span>`);
  if (j.employment_type) b.push(`<span class="badge">${esc(j.employment_type)}</span>`);
  if (j.yoe != null)
    b.push(`<span class="badge yoe">${j.yoe === 0 ? "entry level" : esc(j.yoe) + "+ yrs"}</span>`);
  if (j.deadline) {
    const left = Math.ceil((Date.parse(j.deadline + "T23:59:59Z") - Date.now()) / 86400000);
    if (left >= 0)
      b.push(`<span class="badge${left <= 7 ? " urgent" : ""}">⏳ closes ${
        left === 0 ? "today" : left === 1 ? "tomorrow" : "in " + left + "d"}</span>`);
  }
  if (j.role && j.role !== DEFAULT_ROLE)
    b.push(`<span class="badge">${esc(ROLE_LABEL[j.role] || j.role)}</span>`);
  if (j.department) b.push(`<span class="badge">🗂 ${esc(j.department)}</span>`);
  b.push(...h1bBadges(j));
  return b.length ? `<div class="badges">${b.join("")}</div>` : "";
}

// Everything here arrives from third-party job boards and a community-edited
// GitHub README, so nothing reaches the DOM unescaped. The apostrophe matters
// as much as the angle brackets: ids and company names carry them ("Steven's
// Capital Management"), and they sit inside quoted attributes.
const ESCAPES = {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"};
function esc(s){ return (s == null ? "" : String(s)).replace(/[&<>"']/g, c => ESCAPES[c]); }

// A url only becomes an href if it is really http(s) - never javascript:,
// data:, or anything else a board could put in that field.
function safeUrl(u){
  try {
    const p = new URL(u, location.href);
    if (p.protocol === "http:" || p.protocol === "https:") return esc(p.href);
  } catch (e) { /* unparseable - treated as no link at all */ }
  return "";
}

function isoDay(d){  // local calendar day, not UTC - streaks follow your clock
  const x = new Date(d);
  return `${x.getFullYear()}-${String(x.getMonth()+1).padStart(2,"0")}-${String(x.getDate()).padStart(2,"0")}`;
}

function mark(ids, status) {
  const list = (Array.isArray(ids) ? ids : [ids]).filter(id => data.jobs[id]);
  if (!list.length) return;
  // The toggle is decided ONCE, from the row you clicked, and then applied to
  // every id it covers. Toggling each member on its own would leave a cluster
  // half skipped whenever its members did not already agree.
  const target = data.jobs[list[0]].status === status ? "new" : status;
  const today = isoDay(new Date());
  for (const id of list) {
    const j = data.jobs[id];
    j.status = target;
    // record WHEN, so the activity heatmap and streak have a date to plot.
    // interview implies you applied earlier, so it keeps an existing stamp.
    if (target === "applied" || target === "interview") {
      if (!j.applied_on) j.applied_on = today;
    } else {
      delete j.applied_on;
    }
    marks[id] = { status: j.status, applied_on: j.applied_on || null,
                  ts: Date.now(), synced: 0 };
  }
  writeMarks();          // before the render, so a crash mid-paint costs nothing
  render();
  scheduleSave();
}

let saveTimer = null;
function scheduleSave(){ clearTimeout(saveTimer); saveTimer = setTimeout(persist, 1500); }

async function persist(attempt = 0) {
  const c = cfg();
  const pending = Object.entries(marks).filter(([, m]) => !m.synced);
  if (!pending.length) return;
  if (!c.token || !c.owner || !c.repo) {
    toast("Marks kept in this browser ✓ · add a GitHub token (⚙) to sync them to the repo");
    return;
  }
  const api = `https://api.github.com/repos/${c.owner}/${c.repo}/contents/${PATH}`;
  const h = { Authorization: `Bearer ${c.token}`, Accept: "application/vnd.github+json" };
  try {
    // jobs.json is ~1MB and growing. The contents API refuses to return
    // base64 `content` above 1MB, so read the raw media type instead (good to
    // 100MB) and take the blob sha from the parent directory listing, which
    // carries no size limit either.
    const dir = PATH.slice(0, PATH.lastIndexOf("/"));
    const name = PATH.slice(PATH.lastIndexOf("/") + 1);
    const listing = await (await fetch(
      `https://api.github.com/repos/${c.owner}/${c.repo}/contents/${dir}?ref=${c.branch}&t=${Date.now()}`,
      { headers: h })).json();
    if (!Array.isArray(listing)) throw new Error(listing.message || "cannot list data dir");
    const entry = listing.find(f => f.name === name);
    if (!entry) throw new Error(`${name} not found on ${c.branch}`);
    const rawRes = await fetch(`${api}?ref=${c.branch}&t=${Date.now()}`,
      { headers: { ...h, Accept: "application/vnd.github.raw" } });
    if (!rawRes.ok) throw new Error("read HTTP " + rawRes.status);
    const remote = JSON.parse(await rawRes.text());
    const cur = { sha: entry.sha };
    for (const [id, d] of pending) {
      const t = remote.jobs[id];
      if (!t) continue;
      t.status = d.status;
      if (d.applied_on) t.applied_on = d.applied_on; else delete t.applied_on;
    }
    const body = {
      message: "dashboard: update statuses",
      content: btoa(unescape(encodeURIComponent(JSON.stringify(remote, null, 1)))),
      sha: cur.sha, branch: c.branch,
    };
    const r = await fetch(api, { method: "PUT", headers: h, body: JSON.stringify(body) });
    if (r.status === 409 && attempt < 2) return persist(attempt + 1);
    if (!r.ok) throw new Error("HTTP " + r.status);
    // the mark stays in the store until a later fetch shows the repo serving
    // it (see applyMarks); all this records is that it no longer needs sending
    for (const [id, d] of pending) {
      const held = marks[id];      // may have been re-clicked while this ran
      if (held && held.status === d.status && held.applied_on === d.applied_on)
        held.synced = Date.now();
    }
    writeMarks();
    render();
    toast("Saved ✓");
  } catch (e) { toast("Save failed (kept in this browser): " + e.message); }
}

function toast(msg){ const t=$("toast"); t.textContent=msg; t.style.display="block";
  setTimeout(()=>t.style.display="none", 3000); }

$("settings").onclick = () => {
  const c = cfg();
  $("ghOwner").value=c.owner; $("ghRepo").value=c.repo;
  $("ghBranch").value=c.branch; $("ghToken").value=c.token;
  $("dlg").showModal();
};
function saveSettings(){
  localStorage.gh_owner=$("ghOwner").value.trim(); localStorage.gh_repo=$("ghRepo").value.trim();
  localStorage.gh_branch=$("ghBranch").value.trim()||"main"; localStorage.gh_token=$("ghToken").value.trim();
  $("dlg").close(); toast("Settings saved");
}
["fTier","fStatus","fCompany","fSort","fRole","fYoe","fSource","fH1b"]
  .forEach(id => { if ($(id)) $(id).onchange = render; });
$("tiles").addEventListener("click", e => {
  const t = e.target.closest(".tile");
  if (!t) return;
  const cur = $("fTier").value;
  $("fTier").value = (cur === t.dataset.tier) ? "" : t.dataset.tier;  // click again to clear
  render();
});
$("fSearch").oninput = render;
// #list is replaced wholesale on every render, so the handler lives on the
// container instead of on each button
$("list").addEventListener("click", e => {
  // checked first: the "N openings" control is a button inside the same row
  const g = e.target.closest("button[data-group]");
  if (g) {
    const token = g.dataset.group;
    if (expanded.has(token)) expanded.delete(token); else expanded.add(token);
    render();
    return;
  }
  const b = e.target.closest("button[data-act]");
  if (b) mark(b.dataset.ids.split(" "), b.dataset.act);
});
// Folding is a reading preference, so it outlives the tab. Guarded like the
// fSort read above, because adding a tracker is documented as copying a
// dashboard page - one copied before this control existed should lose the
// fold, not the whole page to a throw at load.
if ($("fGroup")) $("fGroup").onchange = () => {
  try { localStorage.group_dupes = $("fGroup").value; } catch (e) { /* private mode */ }
  expanded.clear();
  render();
};
// ---- page identity ------------------------------------------------------
// Tier keys differ per tracker, so their hues are written onto :root here
// rather than being hard-coded in app.css, and the tier filter is built from
// the same list that drives the KPI tiles.
function boot() {
  document.title = T.title;
  $("h1").textContent = T.title;
  for (const [key, , color] of T.tiers)
    document.documentElement.style.setProperty(`--t-${key}`, color);
  $("fTier").innerHTML = '<option value="">All tiers</option>' +
    T.tiers.map(([key, label]) => `<option value="${esc(key)}">${esc(label)}</option>`).join("");
  $("nav").innerHTML = (T.siblings || [])
    .map(s => `<a href="${esc(s.href)}">${esc(s.label)}</a>`).join("");
  $("ghRepo").placeholder = T.repo || "job-monitor";
  try {
    if ($("fGroup") && localStorage.group_dupes === "0") $("fGroup").value = "0";
  } catch (e) { /* private mode: fall back to the default */ }
}

boot();
load();
