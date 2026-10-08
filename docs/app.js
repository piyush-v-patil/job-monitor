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

// A posting's `source` is the scanner's own word for where it came from, and
// it reads like one: "amazon.jobs", "google careers", "jobspy-linkedin". These
// are the names to show instead.
const SOURCE_LABEL = {
  "jobspy-linkedin": "LinkedIn", "jobspy-indeed": "Indeed",
  "jobspy-glassdoor": "Glassdoor", "jobspy-zip_recruiter": "ZipRecruiter",
  "jobspy-google": "Google Jobs", "simplify-github": "Simplify",
  greenhouse: "Greenhouse", lever: "Lever", ashby: "Ashby", workday: "Workday",
  eightfold: "Eightfold", smartrecruiters: "SmartRecruiters", phenom: "Phenom",
  "amazon.jobs": "Amazon", "google careers": "Google",
  "walmart careers": "Walmart", "microsoft careers": "Microsoft",
  "jobs.apple.com": "Apple", "tesla.com": "Tesla", "uber.com": "Uber",
};

// The vocabulary is open-ended - jobspy_board.py builds its source as
// `jobspy-<site>`, so widening `sites:` in the config to [linkedin, indeed]
// invents a value no map here knows. Tidy whatever arrives rather than
// printing it raw, so the config can move without the dashboard following.
function sourceLabel(src) {
  if (SOURCE_LABEL[src]) return SOURCE_LABEL[src];
  const bare = (src || "").replace(/^jobspy-/, "")
                          .replace(/(\.com|\.jobs|-github|\s+careers)$/i, "")
                          .replace(/[-_.]+/g, " ").trim();
  return bare ? bare.replace(/\b[a-z]/g, c => c.toUpperCase()) : "Unknown";
}

// Three kinds of answer, and they must stay distinguishable: nothing selected,
// one of the two LinkedIn groupings, or one named source. The per-source values
// are prefixed so a source can never be read as a grouping keyword - which is
// how this went wrong before it was written out: the old predicate was
// `(src === "linkedin") === isLinkedIn(j)`, so ANY other value, including a
// source name, quietly meant "everything except LinkedIn".
function sourceMatches(j, want) {
  if (!want) return true;
  if (want === "linkedin") return isLinkedIn(j);
  if (want === "direct") return !isLinkedIn(j);
  return (j.source || "") === want.replace(/^src:/, "");
}

function fillSources() {
  const sel = $("fSource");
  if (!sel) return;
  const keep = sel.value;
  const counts = {};
  let li = 0;
  for (const j of Object.values(data.jobs)) {
    const s = j.source || "";
    counts[s] = (counts[s] || 0) + 1;
    if (isLinkedIn(j)) li++;
  }
  // LinkedIn is left out of the per-source list: "LinkedIn only" above is the
  // same set by definition, and offering it twice invites the reader to look
  // for a difference that is not there.
  const order = Object.keys(counts).filter(s => s !== LINKEDIN_SOURCE)
                      .sort((a, b) => counts[b] - counts[a]);
  const rest = Object.values(data.jobs).length - li;
  // The two groupings stay above the individual boards: LinkedIn is a search
  // rather than a listing, so "everything except the sampled board" is a way
  // of reading the feed, not just another source.
  sel.innerHTML =
    '<option value="">Any source</option>' +
    `<option value="linkedin">LinkedIn only (${li.toLocaleString()})</option>` +
    `<option value="direct">Excluding LinkedIn (${rest.toLocaleString()})</option>` +
    order.map(s => `<option value="src:${esc(s)}">${esc(sourceLabel(s))} (${
      counts[s].toLocaleString()})</option>`).join("");
  // Only restore a selection that still exists. A source really can disappear:
  // state.py rewrites an aggregator row's source to the employer's when the
  // employer's own listing turns up, so the last simplify-github row can become
  // a greenhouse one. Falling back to "Any source" shows too much; leaving the
  // dead value selected would show an empty feed and no reason why.
  if (!keep || keep === "linkedin" || keep === "direct"
      || order.includes(keep.replace(/^src:/, ""))) sel.value = keep;
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

// ---- the company directory ----------------------------------------------
// The feed answers "what has been posted"; this answers "who is hiring, and do
// they sponsor" - which is the order you actually work in when sponsorship
// decides where it is worth applying at all. Every employer with a posting
// here, its filing history, and a way through to its own job board, where the
// listing is complete and current in a way a scan's sample never is.
//
// It is built from what the page already holds. The config that names each
// company's ATS token is server-side only and never published to docs/, so the
// board comes out of the posting URLs themselves.

// Boards that put the employer's token in the first path segment
// (.../<token>/<job id>): that path root is the page a human browses.
const TOKEN_BOARD = /(^|\.)(greenhouse\.io|lever\.co|ashbyhq\.com|smartrecruiters\.com|jobvite\.com)$/i;
// Workday posts at <host>/en-US/<site>/job/<path>; everything before /job/ is
// the site's own search page.
const WORKDAY_BOARD = /(^|\.)(myworkdayjobs\.com|myworkdaysite\.com)$/i;
// A hostname that is itself a careers site. The separator has to be a dot:
// "careers-amd.icims.com" starts with the right word and answers 405, while
// "careers.amd.com" is the real thing.
const CAREERS_HOST = /^(careers?|jobs?|apply|talent|recruiting)\.|\.jobs$|(^|\.)applytojob\.com$/i;
// ...and on a company's own domain, the path segment that opens its listing:
// "stripe.com/jobs/search?gh_jid=..." -> "stripe.com/jobs". Matched whole, so
// "nuro.ai/careersitem" and "award.co/position" are left alone.
const CAREERS_PATH = new Set(["careers", "career", "jobs", "open-positions",
                              "open-roles", "openings", "positions", "all-jobs"]);
// Boards that list every employer rather than one. Their URLs name no employer,
// and must be skipped before any rule below: "linkedin.com/jobs/view/123"
// carries a "jobs" segment, and would otherwise hand 1,428 companies a link to
// LinkedIn's generic job search. Only LinkedIn appears today; the rest are
// here because the jobspy fetcher's `sites:` can be widened to them.
const AGGREGATOR = /(^|\.)(linkedin\.com|indeed\.com|glassdoor\.com|ziprecruiter\.com|dice\.com|simplify\.jobs)$/i;
// Hosted ATSes that expose no browsable root we can work out. Their paths look
// inviting - "careers-amd.icims.com/jobs/1234/x" - but the root of that path
// answers 405, and the hostname is an opaque tenant id as often as a company
// name ("egug.fa.us2.oraclecloud.com"). 57 of them are in the tracker today.
// Skipped outright, so neither of the weaker rules can guess a dead link.
const OPAQUE_ATS = /(^|\.)(icims\.com|oraclecloud\.com|taleo\.net|workable\.com|rippling\.com)$/i;

// Does this host look like it belongs to this employer? Any word of the
// company's name, three letters or more, appearing in the hostname: "stripe"
// in stripe.com, "amd" in careers.amd.com, "netflix" in explore.jobs.netflix.net.
// Three letters, not four, because AMD and IBM are companies; two would let
// "EY" match "yello.co", which is how the rule was wrong before it existed.
function ownDomain(company, host) {
  const h = host.toLowerCase();
  return (company || "").toLowerCase().split(/[^a-z0-9]+/)
    .some(w => w.length >= 3 && h.includes(w));
}

function boardLink(company, urls) {
  let site = "", page = "";        // weaker answers: a careers host, a careers path
  for (const u of urls) {
    let p;
    try { p = new URL(u); } catch (e) { continue; }
    const host = p.hostname;
    if (!host || AGGREGATOR.test(host) || OPAQUE_ATS.test(host)) continue;
    const seg = p.pathname.split("/").filter(Boolean);
    if (TOKEN_BOARD.test(host)) {
      // a bare "jobs.smartrecruiters.com" names no company, so it is not an
      // answer - and must not fall through to the weaker rules either
      if (seg.length) return { url: `https://${host}/${seg[0]}`, label: host };
      continue;
    }
    if (WORKDAY_BOARD.test(host)) {
      const i = seg.findIndex(s => s.toLowerCase() === "job");
      if (i > 0) return { url: `https://${host}/${seg.slice(0, i).join("/")}`, label: host };
      continue;
    }
    // Both weaker rules only fire on a host that is plainly the employer's own.
    // Without that test they guess at third-party ATSes we have no list of:
    // "eyglobal.yello.co/jobs" reads like a careers path, is Ernst & Young's
    // posting host, and answers 404. A company's own domain carries its name.
    if (!ownDomain(company, host)) continue;
    if (!site && CAREERS_HOST.test(host)) { site = `https://${host}`; continue; }
    if (!page) {
      const i = seg.findIndex(s => CAREERS_PATH.has(s.toLowerCase()));
      if (i >= 0) page = `https://${host}/${seg.slice(0, i + 1).join("/")}`;
    }
  }
  // the host on its own beats a path within it: "www.amazon.jobs" is the board,
  // "www.amazon.jobs/en/jobs" is a guess at a page inside it
  const best = site || page;
  if (best) return { url: best, label: best.replace("https://", "") };
  // Everything else: the 1,428 companies seen only through LinkedIn, and the
  // hosts that are a homepage rather than a job list ("stripe.com") or mean
  // nothing to a human ("egug.fa.us2.oraclecloud.com"). A search lands on the
  // real careers page in one click, which beats a confident wrong link.
  return { url: "https://www.google.com/search?q=" + encodeURIComponent(company + " careers"),
           label: "search", search: true };
}

// Rebuilt only when the numbers behind it move, so reopening the panel costs
// nothing. Two things move them: a scan (the poll replaces `data` wholesale and
// `updated` moves with it) and your own marks, which is what `markRev` counts -
// without it, applying to a job and reopening the panel would show the old
// "applied" count.
let coCache = null, coCacheTag = "";
let markRev = 0;

function companyIndex() {
  const tag = data.updated + "|" + markRev;
  if (coCache && coCacheTag === tag) return coCache;
  const by = new Map();
  for (const j of Object.values(data.jobs)) {
    let c = by.get(j.company);
    if (!c) by.set(j.company, c = { company: j.company, postings: 0, open: 0,
                                    applied: 0, closed: 0, urls: [] });
    c.postings++;
    if (isClosed(j)) c.closed++;
    else if (j.status === "new") c.open++;
    if (j.status === "applied" || j.status === "interview") c.applied++;
    // employer_url first: on a board row that is the employer's own posting,
    // which is exactly what the board rules above read. A handful is plenty -
    // every posting of one company resolves to the same board.
    for (const u of [j.employer_url, isLinkedIn(j) ? "" : j.url])
      if (u && c.urls.length < 8) c.urls.push(u);
  }
  for (const c of by.values()) {
    c.h1b = h1b[c.company];
    c.link = boardLink(c.company, c.urls);
    // -1, not 0: "no record" and "looked up, filed nothing" are different
    // answers, and neither should sort as though the employer filed zero
    c.filed = c.h1b ? c.h1b.filed : -1;
  }
  coCache = [...by.values()];
  coCacheTag = tag;
  return coCache;
}

function renderCompanies() {
  const all = companyIndex();
  const q = $("coSearch").value.trim().toLowerCase();
  const sponsors = $("coSponsors").checked, noStaffing = $("coStaffing").checked;
  const rows = all.filter(c =>
    (!q || c.company.toLowerCase().includes(q)) &&
    (!sponsors || !!c.h1b) &&
    (!noStaffing || !(c.h1b && c.h1b.staffing)));
  const sort = $("coSort").value;
  rows.sort(sort === "roles" ? (a, b) => b.open - a.open || b.postings - a.postings
          : sort === "name"  ? (a, b) => a.company.localeCompare(b.company)
          : (a, b) => b.filed - a.filed || b.open - a.open);
  $("coStats").textContent = rows.length === all.length
    ? `${all.length.toLocaleString()} companies`
    : `${rows.length.toLocaleString()} of ${all.length.toLocaleString()} companies`;
  $("coList").innerHTML = rows.map(coRow).join("")
    || "<p style='color:var(--muted);padding:8px 2px'>No company matches.</p>";
}

function coRow(c) {
  // "3 open · 3 tracked" says one thing twice, which is most companies here
  const counts = [c.open ? `${c.open} open` : "", c.applied ? `${c.applied} applied` : "",
                  c.closed ? `${c.closed} closed` : "",
                  c.postings === c.open ? "" : `${c.postings} tracked`
                 ].filter(Boolean).join(" · ");
  const link = safeUrl(c.link.url);
  const to = c.link.search
    ? `Search the web for this employer's careers page - we have only ever seen them through LinkedIn, which names no board`
    : `Open this employer's own job board (${c.link.label}), where the listing is complete`;
  return `
    <div class="job co">
      <div class="info">
        <span class="title">${esc(c.company)}</span>
        <div class="meta">${counts}</div>
        ${h1bBadges(c.h1b).length ? `<div class="badges">${h1bBadges(c.h1b).join("")}</div>` : ""}
      </div>
      <div class="btns">
        <button data-company="${esc(c.company)}" title="${esc(c.open
          ? `Show the ${c.open} open role${c.open === 1 ? "" : "s"} this tracker holds from them`
          : "Nothing open from them right now - this shows everything we have tracked")
        }">${c.open ? `${c.open} open` : "in feed"} →</button>
        ${link ? `<a class="colink" href="${link}" target="_blank" rel="noopener"
             title="${esc(to)}">↗ ${esc(c.link.label)}</a>` : ""}
      </div>
    </div>`;
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
    // a scan landing while the company panel is open should move its numbers
    // rather than leave it showing the counts from before
    if ($("codlg") && $("codlg").open) renderCompanies();
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
const STATUS_WORD = { open:"open", applied:"applied", skip:"skipped",
                     interview:"in interview", closed:"closed", "":"tracked" };

// `closed_at` is written by monitor/expire.py when the employer's own page says
// the posting stopped accepting applications (or 404s). It is a fact about the
// posting, not one of your marks, so it lives beside `status` rather than in
// it: a job you applied to keeps reading "applied" after it closes.
//
// The open feed is the one place it has to win. A closed posting is not
// something to apply to, so "Open (new)" drops it, "Closed" is where it goes,
// and every other view still shows it with the badge on the row.
// `status: "closed"` is the other spelling the database allows (see
// monitor/state.py); nothing writes it today, and reading both means a hand-set
// one still leaves the open feed rather than sitting in it unmarked.
const isClosed = j => !!j.closed_at || j.status === "closed";
function statusMatches(j, status) {
  if (status === "") return true;                       // Everything
  if (status === "closed") return isClosed(j);
  if (status === "open") return j.status === "new" && !isClosed(j);
  return j.status === status;
}

// ---- duplicate requisitions ---------------------------------------------
// Big employers post one role as many separate reqs: 22 "Software Engineer III"
// in Bentonville, 16 "Lead Software Engineer, Full Stack" in McLean. Each is a
// real requisition with its own id and its own apply link, so the scanner is
// right to keep them apart - it is the *view* that drowns, one company's hiring
// push crowding everything else off the screen. They fold into a single row
// here, and the "N openings" badge opens the full list: nothing is hidden, and
// nothing about the stored data changes.
const norm = s => (s || "").replace(/\s+/g, " ").trim().toLowerCase();
const groupKey = j => [j.company, j.title, j.location].map(norm).join("\u0000");

// One role can also be filed under several locations, and then the two copies
// of it never meet: the employer's own board says "3 Locations" or "Remote US"
// while LinkedIn names a metro, so company and title match and the location
// never does. 247 rows in the software tracker are that exact pair, and they
// read as two unrelated openings.
//
// `roleKey` is the weaker key those copies do share. It is only ever used to
// fold a family the location already split, and only when one side declines to
// name a city - two named cities are treated as two openings, because they
// often are. Nothing is dropped either way: this is the view, the stored rows
// are untouched, and the folded row opens to the full list.
const roleKey = j => [j.company, j.title].map(norm).join("\u0000");
// Mirrors NON_CITY in monitor/state.py. Judged on the first comma-field only:
// "Costa Mesa, California, United States" names a city, "2 Locations" does not.
const NON_CITY = /^(|\d+ locations?|multiple locations|remote.*|united states|us|usa|anywhere)$/;
const vagueCity = j => NON_CITY.test(norm((j.location || "").split(",")[0]));

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
    else by.set(k, { token: keyToken(k), members: [row], key: k });
  }
  const out = [];
  for (const g of (on ? foldRoles(by) : by).values()) {
    // the representative is whichever member the active sort puts first, so a
    // folded row answers "newest posted" - or any other sort - honestly, and
    // the date on its face is the freshest of the reqs it stands for
    if (g.members.length > 1) g.members.sort(cmp);
    g.rep = g.members[0];
    // a group that spans locations has to say so on its members, since
    // location is no longer what they were grouped on
    g.spans = g.members.some(m => norm(m[1].location) !== norm(g.rep[1].location));
    out.push(g);
  }
  return out;
}

// Second pass: join the location-split groups of one role back together, where
// some copy of it declined to name a city. Keyed on the role, so which groups
// merge never depends on the order they arrived in.
function foldRoles(by) {
  const families = new Map();
  for (const g of by.values()) {
    const rk = roleKey(g.members[0][1]);
    (families.get(rk) || families.set(rk, []).get(rk)).push(g);
  }
  const out = new Map();
  for (const [rk, groups] of families) {
    const fold = groups.length > 1 && groups.some(g => g.members.some(m => vagueCity(m[1])));
    if (!fold) {
      for (const g of groups) out.set(g.key, g);
      continue;
    }
    out.set(rk, { token: keyToken(rk), key: rk,
                  members: groups.flatMap(g => g.members) });
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
      // ...and on the same day, confidence breaks the tie: a date the board
      // stated outranks one worked out from when we first saw the posting,
      // which outranks a row that only has a discovery date - "first seen
      // today" covers a job posted last week, and letting those tie is what
      // buried today's real postings under the rest of the day's sweep.
      const rank = j => (!j.posted_at ? 2 : approx(j) ? 1 : 0);
      const ra = rank(a[1]), rb = rank(b[1]);
      if (ra !== rb) return ra - rb;
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
      statusMatches(j, status) &&
      (!comp || j.company === comp) &&
      (!role || (j.role || DEFAULT_ROLE) === role) &&
      (!yoe || (yoe === "unstated" ? j.yoe == null : j.yoe != null && j.yoe <= +yoe)) &&
      sourceMatches(j, src) &&
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
        `${n} separate requisitions for this role${
          g.spans ? ", across the locations it is listed under" : " at the same location"}. ` +
        (open ? "Hide them." : "Show them all."))}">${
        open ? "\u25be" : "\u25b8"} ${n} openings</button>`
    : "";
  return `<div class="grp${open ? " open" : ""}">${row(id, j, dupes)}${
    open ? `<div class="members">${g.members.map(m => row(m[0], m[1], "", true)).join("")}</div>` : ""
  }</div>`;

  function row(rid, rj, lead, member = false) {
    // A LinkedIn row's own link goes to LinkedIn, which is a search result and
    // not an application. Where the scanner recognised the employer's own
    // posting for the same role it stored that link too (state.employer_link),
    // and that is the one worth opening. The board link stays on the row: the
    // match is a judgement, and a wrong one has to be one click from recovery.
    const href = safeUrl(rj.employer_url) || safeUrl(rj.url);
    const board = safeUrl(rj.employer_url) && safeUrl(rj.url);
    // a posting whose url will not pass as http(s) still shows, just not as a link
    const title = href
      ? `<a class="title" href="${href}" target="_blank" rel="noopener">${esc(rj.title)}</a>`
      : `<span class="title">${esc(rj.title)}</span>`;
    const alt = board
      ? ` <a class="alt" href="${board}" target="_blank" rel="noopener"
             title="The board listing this was found on">\u2197 LinkedIn</a>`
      : "";
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
      ? [g.spans ? esc(rj.location) : "", postedLabel(rj),
         rj.source ? esc(sourceLabel(rj.source)) : ""].filter(Boolean).join(" \u00b7 ")
      : `${esc(rj.company)} \u00b7 ${esc(rj.location)} \u00b7 ${postedLabel(rj)}`;
    return `
    <div class="job${member ? " member" : ""} ${isClosed(rj) ? "closed " : ""}${
      rj.status === "applied" ? "applied" : rj.status === "skip" ? "skip" : ""}">
      ${rj.status === "new" && !isClosed(rj) ? '<span class="newdot"></span>' : ""}
      <div class="info">
        ${title}${alt}
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
  // the same rule the feed uses, so the chip and the list never disagree
  const openNow = all.filter(j => j.status === "new" && !isClosed(j)).length;
  const closedNow = all.filter(isClosed).length;
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
    ["Open", openNow], ["Applied", count("applied")],
    ["Interview", count("interview")], ["Skipped", count("skip")],
    ["Closed", closedNow],
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
//
// Takes the record rather than the posting, so the feed and the company
// directory cannot drift into saying different things about one employer.
function h1bBadges(h){
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

// `posted_approx` marks a date this repo worked out rather than read off a
// board (monitor/backfill.py, monitor/expire.py). It is good to about a day,
// which is enough to sort by and not enough to quote, so it is never printed
// as though the employer had stated it.
const approx = j => !!j.posted_approx;
const postedLabel = j =>
  j.posted_at ? `${approx(j) ? "\u2248 " : ""}posted ${esc(j.posted_at)}`
              : `first seen ${esc(j.first_seen)}`;

function badges(j, lead = ""){
  const b = lead ? [lead] : [];
  // first, and loudest: it decides whether the rest of the row is worth reading
  if (isClosed(j))
    b.push(`<span class="badge closed" title="${esc(
      `The employer's own page stopped accepting applications, as of ${j.closed_at}.`)
    }">✖ no longer accepting</span>`);
  if (j.comp) b.push(`<span class="badge comp">💰 ${esc(j.comp)}</span>`);
  const age = daysOld(j.posted_at);
  if (age !== null && age <= 3)
    b.push(`<span class="badge fresh"${approx(j)
      ? ' title="Worked out from when this posting was first seen, not stated by the board"' : ""
    }>🔥 ${approx(j) ? "\u2248" : ""}${age <= 0 ? "today" : age + "d ago"}</span>`);
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
  b.push(...h1bBadges(h1bOf(j)));
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
  // No url at all is not a relative one: `new URL(undefined, page)` resolves to
  // the dashboard's own address plus "/undefined", which would pass every test
  // below and put a dead link on the row. Callers now pass fields that are
  // often absent (employer_url), so this has to be the first thing checked.
  if (!u) return "";
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
  markRev++;             // the company directory counts these; let its cache go
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

// ---- the company directory's controls -----------------------------------
// Guarded throughout, like the fGroup and fSort reads: adding a tracker is
// documented as copying a dashboard page, and one copied before this existed
// should lose the panel rather than the whole script to a throw at load.
if ($("companies")) {
  $("companies").onclick = () => {
    // the button is live from the first paint, the data arrives a moment later
    if (!data) { toast("Still loading the tracker..."); return; }
    renderCompanies();
    $("codlg").showModal();
    $("coSearch").select();
  };
  // the index is built on first open, so none of these pay for the feed
  $("coSearch").oninput = renderCompanies;
  ["coSort", "coSponsors", "coStaffing"].forEach(id => { $(id).onchange = renderCompanies; });
  // clicking the backdrop closes it, which is what the dimmed page implies
  $("codlg").addEventListener("click", e => { if (e.target === $("codlg")) $("codlg").close(); });
  // #coList is replaced wholesale on every keystroke, so the handler lives on
  // the container rather than on each row's button
  $("coList").addEventListener("click", e => {
    const b = e.target.closest("button[data-company]");
    if (!b) return;
    showCompany(b.dataset.company);
  });
}

// "Show me what I already have from them" - the company filter doing the work.
function showCompany(name) {
  if (![...$("fCompany").options].some(o => o.value === name)) fillCompanies();
  if (![...$("fCompany").options].some(o => o.value === name)) {
    toast(`${name} is no longer in this tracker`);
    return;
  }
  $("fCompany").value = name;
  // a search term or a tier left over from before can hide every row of the
  // company you just asked for, and landing on "Nothing matches" would break
  // the one click this promises. Role, experience and sponsorship are standing
  // preferences you set deliberately, so they stay.
  $("fSearch").value = "";
  $("fTier").value = "";
  const row = companyIndex().find(c => c.company === name);
  if (row && !row.open) {
    $("fStatus").value = "";          // nothing open: show what there is
    toast(`Nothing open at ${name} - showing everything tracked`);
  }
  $("codlg").close();
  render();
  $("list").scrollIntoView({ behavior: "smooth", block: "start" });
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
