# ATS Alert Pipeline

Polls Greenhouse, Lever, and Ashby public job-board APIs directly (the same
data source companies post to before jobs syndicate to LinkedIn/Indeed),
filters for roles matching your background, and sends a Slack alert the
moment a new match shows up. Runs entirely on GitHub Actions — no server,
no Docker, no laptop required to be on.

## Why this exists

ATS platforms expose free, public, unauthenticated JSON endpoints meant for
embedding job boards on company career pages. Polling those directly gets
you the posting the moment it's live — no aggregator lag, no LinkedIn delay.

## Setup

1. **Push this repo to GitHub as a public repo.** This matters: scheduled
   (`cron`) workflows are disabled on private repos on GitHub's free tier.
   None of the code here is sensitive — your actual filters live in
   `config.json`, which you can keep generic, or gitignore and manage
   locally if you'd rather not have your target companies public. Secrets
   (Slack URL, search API key) never go in the repo regardless — see below.

2. **Add repo secrets** (Settings → Secrets and variables → Actions):
   - `SLACK_WEBHOOK_URL` — an incoming webhook URL for the Slack channel
     you want alerts in.
   - `SEARCH_API_KEY` — (optional, for auto-discovery) a Brave Search API
     subscription token from https://api.search.brave.com/app/keys. Free
     tier is 2,000 queries/month; this pipeline uses ~12/week. Want a
     different search provider instead? Only `search_web()` in
     `discover_companies.py` needs to change.

3. **Edit `config.json`** to match your actual target roles/locations.
   The starter values are placeholders based on EE/AI-ML/data-science
   internship search.

4. **Edit `data/companies.json`** to seed any companies you already know
   you want watched. Find a company's slug from its careers URL:
   - Greenhouse: `boards.greenhouse.io/{slug}`
   - Lever: `jobs.lever.co/{slug}`
   - Ashby: `jobs.ashbyhq.com/{slug}`

5. Workflows run automatically once pushed:
   - `scrape-and-alert.yml` — every 20 minutes
   - `discover-companies.yml` — weekly (Mondays)

   You can also trigger either manually from the Actions tab
   (`workflow_dispatch`) to test without waiting for the schedule.

## How it works

```
scrape.py           → hits every company's ATS endpoint concurrently,
                       normalizes results, isolates per-company failures
                       so one dead board never blocks the batch
filter_and_alert.py → filters by config.json keywords/locations, diffs
                       against previously-seen jobs, Slack-alerts on new
                       matches, and flags if total volume craters
                       (probably a broken scraper, not a hiring freeze)
discover_companies.py → weekly search-based discovery of new company
                         boards, validates each candidate with a real
                         API call before adding it permanently
```

State (`data/seen_jobs.json`, `data/companies.json`,
`data/volume_baseline.json`) is committed back to the repo after each run,
so it survives between GitHub Actions runs without needing a database.

## Known limitations

- **Workday** has no stable public JSON pattern across companies (each
  tenant's endpoint is discovered via browser devtools, not documented).
  Left disabled by default; enable per-company in `config.json` if you
  find and hardcode a specific tenant's endpoint.
- **Companies with no ATS at all** (custom career page, no Greenhouse/
  Lever/Ashby) aren't covered by this pipeline. That's a fundamentally
  different, more brittle scraping problem (HTML diffing) — not
  implemented here; add only for specific companies you decide are worth it.
- **Discovery is best-effort, not exhaustive.** It's a lightweight
  search-based net, not a full web crawl. It supplements manual seeding,
  it doesn't replace your own judgment about companies you already
  know you want watched.

## Extending

- Add a company: append its slug to the right array in `data/companies.json`.
- Change what counts as a match: edit `config.json`.
- Add a new ATS platform: write a `fetch_<platform>()` function in
  `ats_clients.py` returning the same normalized job dict shape, register
  it in `FETCHERS`, and add it to `config.json`'s `platforms` block.
