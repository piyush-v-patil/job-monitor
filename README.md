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
              Tesla/Uber + SimplifyJobs GitHub aggregator)
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

Add `--profile supplychain` and the same pipeline runs over
`config/companies-supplychain.yaml`, `monitor/filters_scm.py` and
`docs/data/supplychain.json` instead. Its own cron
(`.github/workflows/scan-supplychain.yml`) does exactly that every 2 hours,
offset from the software crons.

The dashboards are static pages sharing `docs/app.js` + `docs/app.css`; each
declares a small `TRACKER` object saying which database it reads and what its
tiers and role buckets are called, and writes your Applied/Skip status back
through the GitHub API.

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
│                                    · aggregators: SimplifyJobs repos, ditto
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
│       └── simplify.py            ← parses the SimplifyJobs GitHub repos
│                                    (New-Grad-Positions, Summer2027-
│                                    Internships). Catches Meta, LinkedIn,
│                                    and hundreds of companies with no
│                                    public API. Only rows newer than
│                                    `max_age_days` are considered.
│
├── docs/                          ← served by GitHub Pages
│   ├── app.css                    ← all dashboard styling, shared by both
│   │                                pages. Tier hues are NOT here: each
│   │                                page declares its own, since the two
│   │                                trackers name different tiers.
│   ├── app.js                     ← all dashboard behaviour, shared by both
│   │                                pages: filtering, the KPI band, the
│   │                                activity heatmap, and saving statuses
│   │                                back through the GitHub API (your token
│   │                                stays in your browser's localStorage
│   │                                only). Reads window.TRACKER for
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
    └── scan-supplychain.yml       ← cron "0 */2 * * *": the supply-chain
                                     sweep. Its own concurrency group, so it
                                     can run alongside a software scan —
                                     they write different files
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

The dashboard needs permission to write statuses back to the repo:

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
   "Saved ✓" and, on GitHub, a commit `dashboard: update statuses`.
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
| Test locally without side effects | `pip install -r requirements.txt` then `python -m monitor.main --tier all --dry-run`, or `python -m monitor.main --profile supplychain --tier all --dry-run` |
| Run the unit tests | `pip install -r requirements-dev.txt` then `python -m pytest tests -q`. Covers the filter/tier rules, the id scheme, jobs.json reconciliation, and Discord delivery. CI runs them on every push to `monitor/`. |
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
- **Meta & LinkedIn** have no stable public careers API; they arrive via the
  SimplifyJobs aggregator, typically within a day of posting.
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
