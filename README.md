# Job Monitor

A self-hosted, zero-server monitor for newly posted **US software engineering
jobs** (intern / new grad / experienced up to ~5 yrs) across **big tech,
finance & fintech, Fortune 500, and high-growth startups**.

- **GitHub Actions** runs the scans on a schedule (a full sweep of everything
  every 2 hours, with big tech again on the off hour, so the biggest names are
  checked hourly). No server, no cost.
- **Discord** receives an alert for every genuinely new posting, with a direct
  apply link. The same job ID is **never notified twice**.
- **A web dashboard** (GitHub Pages) shows everything found so far and lets
  you mark roles **Applied / Skip / Interview**. Your marks are saved back to
  the repo and survive forever.

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
             filters: US-only · SWE + adjacent roles · tier
             detection · staff/principal/senior excluded
                                         │
             compare against docs/data/jobs.json (the database,
             committed back into this repo after every run)
                                         │
              new jobs only → Discord webhook notification
```

The dashboard is a single static HTML page that reads `docs/data/jobs.json`
and writes your Applied/Skip status back through the GitHub API.

---

## 2. What every file does

```
job-monitor/
├── README.md                      ← this file
├── requirements.txt               ← Python dependencies (requests, PyYAML)
├── .gitignore                     ← keeps __pycache__ etc. out of git
│
├── config/
│   └── companies.yaml             ← THE COMPANY LIST. Three sections:
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
│   │                                --tier bigtech|other|all, --dry-run,
│   │                                --include-senior
│   ├── filters.py                 ← ALL FILTERING RULES as regexes:
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
│   │                                Reads DISCORD_WEBHOOK_URL from env.
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
│       │                            Microsoft, Google, Apple, Tesla, Uber.
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
│   ├── index.html                 ← THE DASHBOARD. Single self-contained
│   │                                page: filters by tier/status/company,
│   │                                search, Applied/Skip/Interview buttons.
│   │                                Saves statuses back to the repo via the
│   │                                GitHub API (your token stays in your
│   │                                browser's localStorage only).
│   └── data/
│       └── jobs.json              ← THE DATABASE. One entry per job ever
│                                    seen: company, title, tier, location,
│                                    url, first_seen, status. Ships empty;
│                                    Actions commits updates after each run.
│
└── .github/workflows/
    ├── scan-bigtech.yml           ← cron "30 1-23/2 * * *" (odd hours UTC):
    │                                runs `python -m monitor.main --tier
    │                                bigtech`, commits jobs.json if changed
    └── scan-all.yml               ← cron "30 */2 * * *" (even hours UTC,
                                     :30): full sweep including
                                     Fortune 500, fintech, startups, and
                                     the Simplify aggregator
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

### Step 5 — Enable workflows and run the seed scan

1. Repo → **Actions** tab. If prompted "Workflows aren't being run on this
   repository", click **I understand my workflows, go ahead and enable them**.
2. In the left sidebar click **Full sweep (every 2h)** → **Run workflow** →
   green **Run workflow** button.
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
3. After ~1 minute your dashboard is live at
   `https://<YOUR-USERNAME>.github.io/job-monitor/`.

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
| Add/remove a company | `config/companies.yaml` (see comment at top for how to find a company's Greenhouse/Lever/Ashby/Workday token) |
| Change scan frequency | the `cron:` lines in `.github/workflows/*.yml` — times are **UTC** |
| Include Senior titles | add `--include-senior` to the `run:` command in the workflows |
| Change role/location rules | the regexes in `monitor/filters.py` |
| Wider/narrower aggregator window | `max_age_days` under `aggregators:` in the config |
| Test locally without side effects | `pip install -r requirements.txt` then `python -m monitor.main --tier all --dry-run` |
| Run the unit tests | `pip install -r requirements-dev.txt` then `python -m pytest tests -q`. Covers the filter/tier rules, the id scheme, jobs.json reconciliation, and Discord delivery. CI runs them on every push to `monitor/`. |
| Drop tracked postings that are not US | `python -m monitor.prune --dry-run` to review, then without the flag to save. Re-applies the current location rules to `jobs.json`; anything you have already marked (status past `new`) is reported and kept. |

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
