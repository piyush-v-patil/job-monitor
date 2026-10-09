# Job Monitor

A self-hosted, zero-server monitor for newly posted **US jobs**, running **two
independent trackers** off one engine:

| Tracker | `--profile` | Scope | Database | Dashboard | Discord secret |
|---|---|---|---|---|---|
| **Software** | `tech` (default) | SWE + adjacent, intern → ~5 yrs | `docs/data/jobs.json` | `docs/index.html` | `DISCORD_WEBHOOK_URL` |
| **Supply chain** | `supplychain` | Demand planning, forecasting & adjacent planning roles, analyst → manager | `docs/data/supplychain.json` | `docs/supplychain.html` | `DISCORD_WEBHOOK_URL_SUPPLYCHAIN` |

The two share every piece of machinery — fetchers, de-duplication, state,
source-health alerting, the dashboard code — and differ only in the four
things listed in `monitor/profiles.py`: which companies to scan, which role
rules admit a posting, which file to write, and which webhook to notify. A fix
to the engine lands on both at once. The software tracker covers **big tech,
finance & fintech, Fortune 500, and high-growth startups**; the supply-chain
tracker covers **CPG & food, medtech & pharma, semis & hardware, retail,
defense/space, logistics and the DTC brands** that run real planning teams.

- **GitHub Actions** runs the scans on a schedule (a full sweep of everything
  every 2 hours, with big tech again on the off hour, so the biggest names are
  checked hourly). No server, no cost.
- **Discord** receives an alert for every genuinely new posting, with a direct
  apply link. The same job ID is **never notified twice**. Each tracker
  notifies its own webhook, so the two feeds never mix.
- **A web dashboard** (GitHub Pages) per tracker shows everything found so far
  and lets you mark roles **Applied / Skip / Interview**. Your marks are saved
  back to the repo and survive forever. The two pages link to each other in
  the header.

---

## 1. How it works (30-second version)

```
                     ┌────────────────────────────────────────────┐
 GitHub Actions cron │  every 2h  → scan everything (45+ sources) │
                     │  +1h offset→ scan big tech (12 companies)  │
                     └───────────────────┬────────────────────────┘
                                         │
             fetchers pull JSON from public careers APIs
             (Greenhouse, Lever, Ashby, Workday, Eightfold,
              SmartRecruiters + Amazon/Microsoft/Google/Apple/
              Tesla/Uber + SimplifyJobs GitHub aggregator
              + LinkedIn and friends via JobSpy)
                                         │
             filters: US-only · the profile's role rules · tier
             detection (software: staff/principal/senior excluded;
             supply chain: director and above excluded)
                                         │
             compare against the profile's database file (committed
             back into this repo after every run)
                                         │
              new jobs only → that profile's Discord webhook
```

A daily pass then works over the postings already tracked, rather than the ones
arriving: `monitor/backfill.py` recovers the dates and employer links derivable
from the file itself, and `monitor/expire.py` asks each posting whether it still
accepts applications, so the feed stops offering roles that have closed. Both
only ever add fields — your own marks are never touched.

Add `--profile supplychain` and the same pipeline runs over
`config/companies-supplychain.yaml`, `monitor/filters_scm.py` and
`docs/data/supplychain.json` instead. Its own cron
(`.github/workflows/scan-supplychain.yml`) does exactly that every 2 hours,
offset from the software crons.

The dashboards are static pages sharing `docs/app.js` + `docs/app.css`; each
declares a small `TRACKER` object saying which database it reads and what its
tiers and role buckets are called. Your Applied/Skip marks are stored in the
browser as you make them and synced back through the GitHub API when a token
is set.

---

## 2. What every file does

```
job-monitor/
├── README.md                      ← this file
├── requirements.txt               ← Python dependencies (requests, PyYAML)
├── .gitignore                     ← keeps __pycache__ etc. out of git
│
├── config/
│   ├── companies-supplychain.yaml ← THE SUPPLY-CHAIN COMPANY LIST. Same
│   │                                shape as the file below. Every source
│   │                                in it was verified live before being
│   │                                added: the endpoint answers AND returns
│   │                                planning titles. Entries may carry a
│   │                                `searches:` list (see main.py).
│   └── companies.yaml             ← THE SOFTWARE COMPANY LIST. Three
│                                    sections:
│                                    · bigtech:     every 2h, offset 1h (so
│                                    ·                these get hourly cover)
│                                    · other:       every 2h (full sweep)
│                                    · aggregators: SimplifyJobs repos and
│                                    ·                LinkedIn (JobSpy), ditto
│                                    Each entry names a fetcher + its
│                                    parameters (ATS token, Workday tenant…).
│                                    This is the file you'll edit most.
│
├── monitor/                       ← the Python package (the scanner)
│   ├── __init__.py                ← empty; makes `monitor` importable
│   ├── main.py                    ← ENTRYPOINT. Reads the config, runs all
│   │                                fetchers in parallel, filters results,
│   │                                dedupes against the database, saves new
│   │                                jobs, triggers Discord. CLI flags:
│   │                                --profile tech|supplychain, --tier
│   │                                bigtech|other|all, --dry-run,
│   │                                --include-senior.
│   │                                A company with a `searches:` list is
│   │                                fetched once per term and merged — one
│   │                                phrase never covers a whole job family
│   │                                on a keyword-search board.
│   ├── profiles.py                ← THE TWO TRACKERS. One Profile entry per
│   │                                tracker naming its config, database,
│   │                                dashboard, webhook env var, filter
│   │                                module and tier vocabulary. Adding a
│   │                                third tracker needs no engine changes.
│   ├── filters_scm.py             ← SUPPLY-CHAIN FILTERING RULES: which
│   │                                titles count as planning/forecasting,
│   │                                the other professions that own the same
│   │                                words (FP&A, media planning, facilities,
│   │                                maintenance planners, recruiting
│   │                                "sourcing"), hourly/shift exclusions,
│   │                                tiers (intern/entry/mid/manager) and
│   │                                role families. Shares this file's
│   │                                location and years-of-experience rules.
│   ├── filters.py                 ← SOFTWARE FILTERING RULES as regexes:
│   │                                which titles count as SWE/adjacent,
│   │                                tier detection (intern/newgrad/
│   │                                experienced), staff/principal/senior
│   │                                exclusion, US-location detection.
│   │                                Edit this to widen or narrow scope.
│   ├── state.py                   ← the "database" layer. Reads/writes
│   │                                docs/data/jobs.json, generates stable
│   │                                job IDs (company + hash of job ID/URL),
│   │                                appends only unseen jobs, NEVER touches
│   │                                your Applied/Skip statuses.
│   ├── notify.py                  ← Discord webhook sender. Batches embeds
│   │                                (10 per message), handles rate limits.
│   │                                Reads whichever env var the running
│   │                                profile names, and labels tiers with
│   │                                that profile's vocabulary.
│   │
│   └── fetchers/                  ← one module per data-source type
│       ├── __init__.py            ← FETCHERS registry: maps the `fetcher:`
│       │                            name in companies.yaml to a function
│       ├── http.py                ← shared HTTP session (browser-like
│       │                            User-Agent, retries on 429/5xx)
│       ├── generic.py             ← the 6 generic ATS fetchers. Any company
│       │                            on Greenhouse, Lever, Ashby, Workday,
│       │                            Eightfold, or SmartRecruiters can be
│       │                            added with 3–5 lines of YAML.
│       ├── custom.py              ← company-specific fetchers for careers
│       │                            sites with their own APIs: Amazon,
│       │                            Microsoft, Google, Apple, Tesla, Uber,
│       │                            Walmart, plus `phenom` (PepsiCo, AMD and
│       │                            the many Fortune 500 sites on Phenom —
│       │                            its keyword search is decorative, so the
│       │                            board is paged and filtered locally).
│       │                            These endpoints are unofficial and may
│       │                            change — see Troubleshooting.
│       ├── simplify.py            ← parses the SimplifyJobs GitHub repos
│       │                            (New-Grad-Positions, Summer2027-
│       │                            Internships). Catches Meta, LinkedIn,
│       │                            and hundreds of companies with no
│       │                            public API. Only rows newer than
│       │                            `max_age_days` are considered.
│       └── jobspy_board.py        ← searches the job boards themselves
│                                    (LinkedIn, and optionally Indeed,
│                                    Glassdoor, Google, ZipRecruiter) through
│                                    the `python-jobspy` library. One search
│                                    per term under `searches:`; rows are
│                                    marked so a posting the employer's own
│                                    ATS also gave us is merged, not tracked
│                                    twice. Also puts back the posting date
│                                    JobSpy drops on everything less than a day
│                                    old — LinkedIn tags fresh cards with a
│                                    different `<time>` class, and without it
│                                    today's postings arrive undated and never
│                                    sort to the top. See §5 for the knobs.
│
├── monitor/expire.py              ← asks tracked postings whether they still
│                                    accept applications, and stamps
│                                    `closed_at` on the ones that do not.
│                                    LinkedIn's own page says so in its markup
│                                    (a closed posting loses its apply button);
│                                    a deleted one 404s. Anything else — a
│                                    throttled reply, a sign-in wall, an
│                                    unfamiliar layout — is no verdict, and the
│                                    posting is left exactly as it was. Never
│                                    writes `status`: that is yours, and a job
│                                    you applied to is still one after it
│                                    closes. Run by its own daily workflow.
├── monitor/backfill.py            ← the offline pass: recovers what the tracker
│                                    can work out from its own contents, for
│                                    the rows no future scan will see again.
│                                    Two things: a posting date for LinkedIn
│                                    rows that have none (an undated row is
│                                    undated *because* LinkedIn called it under
│                                    a day old when we found it, so its date is
│                                    its first_seen ± a day — checked against
│                                    LinkedIn's own "N days ago" on 18 rows, 17
│                                    agreed), marked `posted_approx` so a
│                                    derived date never reads as a stated one;
│                                    and the employer's own link for a board
│                                    row, where this repo already holds that
│                                    employer's posting for the same role.
│                                    Contacts nothing. Runs daily ahead of the
│                                    closure sweep; re-running is a no-op.
├── monitor/prune.py               ← re-applies the CURRENT location rules to
│                                    postings already stored, since a filter
│                                    fix only changes what future scans admit.
│                                    Anything you have marked is reported and
│                                    kept, never dropped.
├── monitor/h1b.py                 ← builds docs/data/h1b.json: for every
│                                    company either tracker has seen, how many
│                                    H-1B petitions that employer has filed,
│                                    how recently, and whether it files as a
│                                    staffing agency. Run by its own weekly
│                                    workflow; see §7 for what the signal does
│                                    and does not mean.
├── monitor/names.py               ← company-name normalization shared by the
│                                    posting de-duplicator and the visa matcher
│
├── docs/                          ← served by GitHub Pages
│   ├── app.css                    ← all dashboard styling, shared by both
│   │                                pages. Tier hues are NOT here: each
│   │                                page declares its own, since the two
│   │                                trackers name different tiers.
│   ├── app.js                     ← all dashboard behaviour, shared by both
│   │                                pages: filtering, the KPI band, the
│   │                                activity heatmap, the folding of
│   │                                duplicate requisitions into one row,
│   │                                the 🏢 Companies panel (every employer
│   │                                with its H-1B history and a link to its
│   │                                own job board, worked out from the
│   │                                posting URLs the tracker already holds —
│   │                                no new file, no workflow),
│   │                                and your Applied/Skip
│   │                                marks — written to this browser's
│   │                                localStorage the moment you click, then
│   │                                synced back through the GitHub API (your
│   │                                token stays in localStorage too, and is
│   │                                never sent anywhere else). A mark is only
│   │                                forgotten once the published JSON is seen
│   │                                carrying it. Reads window.TRACKER for
│   │                                everything page-specific.
│   ├── index.html                 ← SOFTWARE DASHBOARD. Markup plus a
│   │                                TRACKER object naming jobs.json, its
│   │                                tiers and its role labels.
│   ├── supplychain.html           ← SUPPLY-CHAIN DASHBOARD. Same markup,
│   │                                pointed at supplychain.json with its
│   │                                own tiers and planning role labels.
│   └── data/
│       ├── jobs.json              ← THE SOFTWARE DATABASE. One entry per
│       │                            job ever seen: company, title, tier,
│       │                            location, url, first_seen, status.
│       └── supplychain.json       ← THE SUPPLY-CHAIN DATABASE, same shape.
│                                    Actions commits updates after each run.
│
└── .github/workflows/
    ├── scan-bigtech.yml           ← cron "30 1-23/2 * * *" (odd hours UTC):
    │                                runs `python -m monitor.main --tier
    │                                bigtech`, commits jobs.json if changed
    ├── scan-all.yml               ← cron "30 */2 * * *" (even hours UTC,
    │                                :30): full sweep including
    │                                Fortune 500, fintech, startups, and
    │                                the Simplify aggregator
    ├── scan-supplychain.yml       ← cron "0 */2 * * *": the supply-chain
    │                                sweep. Its own concurrency group, so it
    │                                can run alongside a software scan —
    │                                they write different files
    ├── h1b-refresh.yml            ← cron "45 4 * * 1" (Mondays): rebuilds
    │                                docs/data/h1b.json. Weekly, not quarterly:
    │                                the DOL data moves each quarter but the
    │                                company list grows daily, and a company
    │                                with no entry gets no badge
    ├── expire.yml                 ← cron "10 3 * * *": runs
    │                                `python -m monitor.expire` over both
    │                                trackers, marking postings that no longer
    │                                accept applications. Paced at ~1.2s per
    │                                posting, so each run takes the ones whose
    │                                information is oldest and the rotation
    │                                comes round over several days
    └── linkedin-smoke.yml         ← manual only. Asks LinkedIn for postings
                                     from a runner and fails loudly if it
                                     gets none, which is how you tell
                                     "throttled" from "broken" without
                                     waiting for a scan
```

---

## 3. Setup guide — from zip to working (~15 minutes)

### Prerequisites

- A GitHub account.
- Git installed (`git --version` in a terminal; download from
  https://git-scm.com if missing).
- A Discord server where you can manage webhooks (any server you own; create
  one free in Discord with **+ Add a Server** if needed).
- (Optional, for local testing) Python 3.10+.

### Step 1 — Create the GitHub repository

1. Go to https://github.com/new
2. Repository name: `job-monitor` (anything works).
3. Visibility: **Public** is simplest (free GitHub Pages). Private also works,
   but Pages on a private repo needs GitHub Pro — see Step 6 for the
   workaround.
4. Do **NOT** check "Add a README" / .gitignore / license (the project already
   has them; an empty repo avoids merge conflicts).
5. Click **Create repository**.

### Step 2 — Push the project

Unzip `job-monitor.zip`, open a terminal **inside the unzipped `job-monitor`
folder** (the one containing `README.md`), and run:

```bash
git init
git add -A
git commit -m "initial commit"
git branch -M main
git remote add origin https://github.com/<YOUR-USERNAME>/job-monitor.git
git push -u origin main
```

Replace `<YOUR-USERNAME>` with your GitHub username. If git asks you to log
in, follow the browser prompt (or use GitHub Desktop / `gh auth login` if you
prefer).

Refresh the repo page — you should see all the folders.

### Step 3 — Create the Discord webhook

1. In Discord, pick (or create) the channel where alerts should land, e.g.
   `#job-alerts`.
2. Server Settings → **Integrations** → **Webhooks** → **New Webhook**.
3. Name it (e.g. "Job Monitor"), select the channel, click
   **Copy Webhook URL**. It looks like
   `https://discord.com/api/webhooks/1234.../AbCd...`. Treat it like a
   password — anyone with it can post to your channel.

### Step 4 — Add the webhook as a repo secret

1. On GitHub: your repo → **Settings** → **Secrets and variables** →
   **Actions** → **New repository secret**.
2. Name: `DISCORD_WEBHOOK_URL` (exactly this, case-sensitive).
3. Secret: paste the webhook URL. Click **Add secret**.

**For the supply-chain tracker**, repeat steps 3–4 with a *second* Discord
webhook (a different channel is the point — two job searches in one channel
is unreadable) and store it as `DISCORD_WEBHOOK_URL_SUPPLYCHAIN`. Until that
secret exists the supply-chain scan still runs and still commits its
database; it just prints `DISCORD_WEBHOOK_URL_SUPPLYCHAIN not set - skipping
notification` instead of messaging you.

### Step 5 — Enable workflows and run the seed scan

1. Repo → **Actions** tab. If prompted "Workflows aren't being run on this
   repository", click **I understand my workflows, go ahead and enable them**.
2. In the left sidebar click **Full sweep (every 2h)** → **Run workflow** →
   green **Run workflow** button. Do the same for **Supply chain sweep
   (every 2h)** to seed the second tracker.
3. Wait 2–4 minutes, then open the run and read the log of the "Run full
   sweep" step. You'll see one line per company (`✓ Amazon: 100 raw
   postings` / `! SomeCompany: FAILED …`) and a summary like
   `2600 raw -> 340 in scope -> 340 new (seed run: notifications suppressed)`.

**Important:** this first run is a **seed run**. It records everything
currently open into the database **without sending any Discord messages** —
otherwise you'd be flooded with hundreds of alerts for old postings. Every
run after this one notifies **only new postings**.

4. Check that the run's last step committed — the repo should now show a
   commit like `scan(all): update jobs.json`, and `docs/data/jobs.json`
   should be full of entries.

A few companies failing is normal (endpoints change, some ATS tokens are
best-effort) — the run continues past them. See Troubleshooting.

### Step 6 — Turn on the dashboard (GitHub Pages)

1. Repo → **Settings** → **Pages**.
2. Under "Build and deployment": Source = **Deploy from a branch**,
   Branch = `main`, Folder = **/docs**. Save.
3. After ~1 minute your dashboards are live at
   `https://<YOUR-USERNAME>.github.io/job-monitor/` (software) and
   `https://<YOUR-USERNAME>.github.io/job-monitor/supplychain.html`
   (supply chain). Each page has a link to the other in its header.

**Private repo without GitHub Pro?** Skip Pages entirely: pull the repo and
open `docs/index.html` directly in your browser — the dashboard works the
same (statuses still save via the API; only the job list needs a
`git pull` to refresh, or click ⚙ and it will still read via your token).

### Step 7 — Enable "mark as Applied" saving

**Optional.** Applied/Skip/Interview is remembered by your browser as soon as
you click it, with or without a token: it survives a refresh, a new scan
landing, and a closed tab. A token is what carries those marks *into the
repo*, so they show up on your other devices and in the JSON itself. Until
one is set, the status bar shows a `THIS BROWSER n` chip counting the marks
that live only here.

To let the dashboard write statuses back to the repo:

1. GitHub → click your avatar → **Settings** → **Developer settings** →
   **Personal access tokens** → **Fine-grained tokens** → **Generate new
   token**.
2. Token name: `job-monitor-dashboard`. Expiration: your choice (you'll
   re-paste it when it expires).
3. Repository access: **Only select repositories** → choose `job-monitor`.
4. Permissions → Repository permissions → **Contents** → **Read and write**.
   Nothing else.
5. Generate, copy the `github_pat_...` value.
6. Open your dashboard → click **⚙ GitHub token** → fill in:
   Owner = your username, Repo = `job-monitor`, Branch = `main`,
   Token = the PAT. Save.

The token is stored **only in your own browser's localStorage** — it is never
committed or sent anywhere except api.github.com.

### Step 8 — Verify end-to-end

1. In the dashboard, click **✓ Applied** on any job → you should see
   "Saved ✓" and, on GitHub, a commit `dashboard: update statuses`. (The mark
   itself shows up immediately either way; the `SYNCING n` chip clears once
   the published file comes back carrying it, a minute or two later after
   Pages redeploys.)
2. Actions tab → run **Scan big tech (every 3h)** manually once → since the
   seed already happened, any *genuinely new* posting now produces a Discord
   message. (If nothing new was posted in the last 3 hours, no message —
   that's correct behavior.)
3. Done. From now on everything is automatic.

---

## 4. Daily use

- New postings arrive in Discord with tier, location, and a direct apply link.
- Open the dashboard (default filter shows **Open (new)**), apply on the
  company site, click **✓ Applied**. Clicking the same button again undoes it.
- **✗ Skip** hides roles you don't want; **★ Interview** tracks progress.
- **Duplicate requisitions fold into one row.** Big employers post the same
  role many times over — 22 separate "Software Engineer III" reqs in
  Bentonville, 16 "Lead Software Engineer" in McLean — and shown in full they
  bury everything else. Postings that match on company, title *and* location
  collapse into a single row carrying a **`N openings`** badge; click it to
  open the full list, each req with its own apply link and buttons. The row
  shows the freshest of them. Nothing is dropped and nothing about the stored
  data changes — these are genuinely distinct reqs, and the scanner still
  tracks each one separately. Switch the header's **Group duplicate reqs** to
  **Show every posting** to see them all inline; the choice is remembered.
- **On a folded row, ✓ Applied marks one req and ✗ Skip clears them all.**
  You apply to a single requisition — marking 22 would log 22 applications on
  the activity heatmap — but dismissing the cluster is the whole point of
  folding it. Expand the row to act on one req at a time.
- **🏢 Companies lists every employer in this tracker**, biggest H-1B sponsor
  first, with staffing agencies badged — several of the heaviest filers are
  consultancies that would place you at a client site. Search it, or narrow it
  to sponsors only. Each row carries two things: **↗ their job board**, which is
  the complete and current listing a scan only samples, and **N open →**, which
  filters the feed to what this tracker already holds from them. A number means
  filings on record; *no H-1B filings found* means a gap in the public data
  rather than a verdict; and a row that says nothing about sponsorship is one
  the index has not looked up yet.
- **The source filter lists every board the feed came from**, each with its
  count — Simplify, Greenhouse, Workday, Amazon, Ashby, Google, Lever, Walmart,
  Eightfold — so you can read one at a time. **LinkedIn only** and **Excluding
  LinkedIn** stay above them as groupings, because LinkedIn is a relevance-ranked
  search rather than a listing and setting it aside is a way of reading the whole
  feed. The names are labels over the scanner's own values (`amazon.jobs`,
  `jobspy-linkedin`), and a board added to the config later appears on its own
  with a tidied name.
- **Closed postings leave the feed on their own.** A daily pass asks each
  tracked posting whether it still accepts applications and marks the ones that
  don't, so **Open (new)** stops offering roles that closed days ago. They are
  not deleted: pick **Closed (no longer accepting)** in the status filter to see
  them, struck through and badged with the date. A job you had already marked
  keeps its mark — "applied, and it has since closed" is worth knowing.
- **Sort: newest posted puts today's postings first.** On the same day,
  confidence breaks the tie: a date the board stated outranks one this repo
  worked out, which outranks a row that only has a discovery date ("first seen
  today" covers a job posted last week). The 🔥 badge marks anything posted in
  the last three days.
- **A date shown as `≈ posted 24 Sep` was derived, not published.** It comes
  either from when the posting was first seen or from LinkedIn's own "5 days
  ago", and it is good to about a day — enough to sort by, not enough to quote.
  A board that later states the real date replaces it.
- **LinkedIn rows link to the employer where we know it.** When this repo also
  holds the employer's own posting for that role, the title opens *that*, and a
  small `↗ LinkedIn` beside it keeps the board listing one click away. About 240
  rows today: the match needs the employer's board to be one we scan, so most
  LinkedIn rows still link to LinkedIn.
- **One role listed under several locations folds into one row.** Employers'
  boards often decline to name a place ("3 Locations", "Remote US") while
  LinkedIn names a metro, which used to read as two unrelated openings. Same
  company and title, with either side vague about the city, now fold — 497 rows
  fewer in the software view — and the expanded list shows each location.
- Applied/skipped roles never re-alert. The scanner only ever *adds* new job
  IDs — it cannot overwrite your statuses.
- **The page keeps itself current.** An open tab checks for a new scan every
  minute (and the moment you switch back to it) and folds in new roles with a
  toast, so you never sit on stale data. Marks you have not saved yet survive
  that refresh.
- **Track your own progress.** Each tier tile counts what you have applied to,
  and the activity heatmap plus streak show applications per day. Marking a
  role Applied stamps the date, so the history builds from your first click.

---

## 5. Customizing

| Want to… | Edit |
|---|---|
| Add/remove a company | `config/companies.yaml` (software) or `config/companies-supplychain.yaml` (supply chain) — see the comment at the top of either for how to find a company's Greenhouse/Lever/Ashby/Workday token |
| Cover more of a keyword-search board | add or edit that entry's `searches:` list. Workday/Eightfold/Amazon/Google/Walmart only answer keyword searches, so each term is a separate pass whose results are merged |
| Change scan frequency | the `cron:` lines in `.github/workflows/*.yml` — times are **UTC** |
| Include Senior titles | add `--include-senior` to the `run:` command in the workflows |
| Change role/location rules | `monitor/filters.py` (software) or `monitor/filters_scm.py` (supply chain). Location and years-of-experience rules live in `filters.py` and are shared — a change there affects both trackers |
| Narrow the supply-chain feed | procurement and logistics are the highest-volume families in it. Drop those alternatives from `ROLE_INCLUDE` in `filters_scm.py`, or just filter to *Demand planning & forecasting* on the dashboard |
| Add a third tracker | a `Profile` entry in `monitor/profiles.py`, a `companies-*.yaml`, a dashboard page (copy `docs/supplychain.html` and edit its `TRACKER`), and a workflow. The engine needs no changes |
| Wider/narrower aggregator window | `max_age_days` under `aggregators:` in the config |
| Which LinkedIn searches run | `searches:` under the `LinkedIn (JobSpy)` entry — one term per job family; the boards rank by relevance, so more terms beat a bigger `results_wanted` |
| How far back LinkedIn looks | `hours_old:` on the same entry (default 72) |
| Read each LinkedIn posting's body | `fetch_description: true` — lets `parse_yoe` correct a tier the title got wrong, at +1 request per job |
| Other boards (Indeed, Glassdoor…) | add to `sites:` on the same entry — `[linkedin, indeed]`. Indeed is the least rate-limited of the set |
| Fix a wrongly matched employer | add the company to `ALIASES` in `monitor/h1b.py`, then re-run the H-1B refresh workflow |
| Route LinkedIn through proxies | set the `JOBSPY_PROXIES` repo secret (comma-separated URLs); the config only names the variable, never holds a credential |
| Change what counts as a duplicate req | `groupKey` in `docs/app.js` — postings are folded when company, title and location all match once whitespace and case are normalized. Folding is a view-only concern; nothing in `monitor/` or the JSON is involved |
| Test locally without side effects | `pip install -r requirements.txt` then `python -m monitor.main --tier all --dry-run`, or `python -m monitor.main --profile supplychain --tier all --dry-run` |
| Run the unit tests | `pip install -r requirements-dev.txt` then `python -m pytest tests -q`. Covers the filter/tier rules, the id scheme, jobs.json reconciliation, and Discord delivery. CI runs them on every push to `monitor/`. |
| Recover missing dates and employer links | `python -m monitor.backfill --dry-run` to review, then without the flag. `--dates` / `--links` to do one only, `--profile supplychain` for the other tracker. Offline — it only reads the tracker's own contents. Runs daily with the closure sweep, so this is for when you want it now. |
| Change how a company's job board is guessed | the rules in `boardLink` in `docs/app.js`, in the order they are tried: token-in-path boards (Greenhouse/Lever/Ashby/SmartRecruiters/Jobvite), Workday, a careers-looking hostname, a careers path on the employer's own domain, then a Google search |
| Never guess a careers page, always search | drop the `CAREERS_HOST` and `CAREERS_PATH` branches from `boardLink`; every company whose ATS this does not recognise then falls through to the search link |
| Change the companies panel's default sort | the first `<option>` of `#coSort` in `docs/index.html` and `docs/supplychain.html` |
| Change what counts as the same role across locations | `roleKey` and `NON_CITY` in `docs/app.js` (the fold) and `NON_CITY` in `monitor/state.py` (the employer-link match). Both judge the first comma-field of the location only — `Costa Mesa, California, United States` names a city, `2 Locations` does not |
| Mark postings that stopped accepting applications | `python -m monitor.expire --dry-run` to review, then without the flag to save. `--limit N` caps the requests (default 500, `0` = every posting), `--delay` paces them, `--profile supplychain` for the other tracker. Runs daily on its own; this is for when you want it now. |
| Drop tracked postings that are not US | `python -m monitor.prune --dry-run` to review, then without the flag to save. Add `--profile supplychain` for the other tracker. Re-applies the current location rules to that tracker's database; anything you have already marked (status past `new`) is reported and kept. |

---

## 6. Troubleshooting

**A company shows `! FAILED` in every run.** Its endpoint or ATS token is
wrong/changed. Open that company's careers page with your browser's network
tab (F12 → Network) and look for requests to `boards-api.greenhouse.io/...`,
`api.lever.co/...`, `jobs.ashbyhq.com/...`, or
`<tenant>.wdX.myworkdayjobs.com/wday/cxs/...`, then correct the entry in
`companies.yaml`. Apple/Google/Tesla/Uber use unofficial endpoints
(`monitor/fetchers/custom.py`) that occasionally change — same technique.

**No Discord messages ever.** Check the secret name is exactly
`DISCORD_WEBHOOK_URL`; check the Actions log — it prints
`DISCORD_WEBHOOK_URL not set` if the secret is missing. Remember the very
first run never notifies (seed), and later runs only notify *new* jobs.

**"Save failed" in the dashboard.** Token expired, or missing
Contents-write permission, or wrong owner/repo/branch in ⚙ settings.

**Workflow stops running after ~60 days.** GitHub disables cron on
repositories with no activity. Any commit re-enables it — but the scanner's
own commits count as activity, so this only matters if all scans fail for
60 days straight.

**jobs.json grows big.** Delete old entries with status `applied`/`skip`
occasionally if you like — or just leave it; a year of use stays in the
low MBs. (Note: past ~1 MB the dashboard's save round-trip may fail due to
a GitHub API limit; prune before that.)

**Runs start late.** GitHub cron is best-effort; a few minutes late is
normal, occasionally more during peak load.

---

## 7. Known limitations (honest list)

- **Unofficial APIs**: the big-tech fetchers use the same JSON endpoints
  the careers sites themselves use — they can change without notice. A
  failing fetcher is logged and skipped, never fatal.
- **Meta & LinkedIn** have no stable public careers API. LinkedIn postings
  arrive two ways: the SimplifyJobs aggregator, and the `jobspy` fetcher,
  which drives LinkedIn's own search endpoints.
- **A board search is a sample, not a listing.** LinkedIn ranks by relevance
  and rate-limits around the 10th page, so the `jobspy` source returns the
  top N for each term in `searches:` rather than everything posted. Widening
  coverage means adding terms, not raising `results_wanted`.
- **LinkedIn rate-limits datacenter IPs**, which is what GitHub's runners
  are. If the source starts reporting 0 postings (the source-health alert
  says so), it is being throttled, not broken: add a `JOBSPY_PROXIES` secret
  and it resumes. `Actions → LinkedIn reachability → Run workflow` answers
  "is it being throttled right now?" without waiting for a scan.
- **Closure is only detected where the board says so out loud.** LinkedIn
  renders "No longer accepting applications" into its public page, so that is
  what `monitor/expire.py` reads; every other board words it differently, and
  postings on them are left alone rather than guessed at. Absence from a scan is
  never taken as closure either — the LinkedIn source only ever asks for the
  last 72 hours, so every posting it finds leaves that window while still open.
- **The company panel's board links are worked out from posting URLs, not
  looked up.** The config files that hold each company's real ATS token are
  server-side only and never published to `docs/`, so the browser has nothing
  else to go on: 516 of 2,039 companies in the software tracker get a direct
  link, and the rest get a Google search for "<company> careers" — mostly the
  1,428 known only through LinkedIn, which names no employer board. A derived
  root can also point at a board the company has since moved off. The weaker
  rules only fire on a host carrying the company's own name, because
  `eyglobal.yello.co/jobs` reads like a careers page, is where Ernst & Young's
  postings live, and answers 404.
- **The panel counts company strings, not employers.** It lists exactly the
  names the tracker holds, so "Walmart" and "Walmart Global Tech" are two rows,
  and the H-1B index's own approximate matches carry their `~` into this view
  unchanged.
- **LinkedIn will not tell us where to apply.** JobSpy reads the employer's
  apply link from a `<code id="applyUrl">` element on the public job page, and
  LinkedIn no longer serves it to logged-out clients — 20 tracked postings
  fetched, none carried it, no JSON-LD either, and the authenticated API answers
  403. So `fetch_description: true` would buy ~2,000 extra requests per scan and
  no link. The employer URLs the dashboard shows are matched against postings
  this repo fetched itself, which caps them at the employers we scan: 236 of
  4,252 LinkedIn rows today.
- **That match is by company and title, so it can point at a sibling req.** It
  is only taken when there is one candidate posting, or one filed under the same
  city or under no city at all; two candidates in two named cities get nothing,
  because two offices advertising one title are two jobs. The board link stays on
  the row so a wrong guess is one click from recovery.
- **A derived date can be a day out, and says so.** `≈` means the date came from
  when the posting was first seen, or from a phrase like "5 days ago", not from
  the board. Checked against LinkedIn's own age on 18 undated postings: 17 were
  within a day, one page stated no age at all.
- **The cross-location fold can put two real openings under one row.** Adobe
  advertises "Software Development Engineer" in six cities; those now read as one
  row with an openings badge. Nothing is dropped — expand it, or switch to *Show
  every posting* — but the row's own location is then the representative's.
- **Roughly a tenth of the LinkedIn feed is already closed at any time.** A
  random sample of 200 tracked postings found 21 closed or deleted, spread
  across every posting date rather than piling up at the old end — the accounts
  that close fastest are the high-volume reposters (`Jobright.ai`,
  `RemoteHunter`, `BeaconFire`), some within a day of posting.
- **A closed posting can sit in the feed for a day or two before it is marked.**
  The check costs one request per posting and LinkedIn throttles bursts, so a
  run rotates through the oldest-known part of the tracker rather than sweeping
  all of it. Raise `--limit` in `expire.yml` to shorten the cycle, at the cost
  of a longer run.
- **H-1B sponsorship is a company's filing history, not a promise about the
  role.** The badge counts petitions the employer has filed with the Department
  of Labor. A company with 400 filings still posts citizenship-only and
  clearance-only reqs, so it narrows the field rather than settling it.
- **No filings found is not "does not sponsor".** Northrop Grumman has zero
  records across 2009–2026 — a hole in the disclosure data, not a fact about
  the employer. The dashboard says "no H-1B filings found" and styles it
  neutrally for exactly that reason, and nothing is ever filtered out by
  default.
- **~16% of companies do not match a filer.** Matching is by normalized name:
  exact, then de-spaced (`WAL-MART` → `Walmart`), then a hand-written alias
  table, then a first-token prefix. The last tier is loose by design — it is
  what lets short names like Uber, Okta and CGI match at all, and it costs a
  few wrong guesses ("Flex" lands on *Flex Consulting Group*, not the
  manufacturer). Loose matches are badged with a `~` and name the matched
  employer on hover, so a bad guess is visible rather than asserted. Widening
  `ALIASES` in `monitor/h1b.py` is the fix for any that matter to you.
- **The visa data is a third-party mirror.** USCIS and DOL both serve their
  bulk files behind bot protection that refuses automated download (403), so
  the index is built from a community mirror of the same public-domain DOL
  disclosures. Every build verifies the download against the sha256 in the
  mirror's own manifest, and `version`/`built_at` are written into
  `docs/data/h1b.json` — so if the mirror stops updating, the badge ages
  visibly rather than breaking.
- **"Experienced ≤5 yrs" is title-based** (SWE II/III, Engineer 2…). Plain
  "Software Engineer" titles are included too — verify the years requirement
  in the actual posting.
- **Some ATS tokens in the config are best-effort** (see comments). A
  `--dry-run` shows you immediately which ones need fixing.
- **The supply-chain tracker is title-based too, and the job family shares
  its vocabulary with half the company.** "Planning", "forecast", "buyer"
  and "sourcing" all belong to other professions, so `filters_scm.py` runs a
  long exclusion list (FP&A, media planning, facilities and campus planning,
  maintenance planners, recruiting "sourcing", HR business partners, hourly
  and shift roles). Titles it cannot place still get through — that costs
  one glance, whereas a wrong exclusion loses the posting silently.
- **Procurement and logistics dominate that feed by volume** (roughly a
  third and a fifth of it). They are genuinely adjacent, so they are kept;
  filter to *Demand planning & forecasting* on the dashboard when you want
  the core discipline only.
- **A company's planning team may not be in the US at all.** General Mills,
  for instance, runs demand planning out of Mumbai — those postings are
  correctly dropped by the US filter, which is why a big CPG name can show
  up with very few rows.
- **Big-box retailers are under-covered.** Their careers sites (Kroger,
  Best Buy, Lowe's, Kraft Heinz, Colgate…) are client-rendered with no
  reachable JSON endpoint, so they are absent rather than half-working. The
  Workday and Phenom tenants that *do* answer were each verified before
  being added.
