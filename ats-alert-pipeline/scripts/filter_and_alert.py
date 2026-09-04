"""
Filters the latest scrape against your keyword/location config, diffs
against previously-seen postings, and sends a Slack alert for anything
new that matches. Also does a lightweight anomaly check: if total job
volume craters versus the rolling baseline, that's more likely a broken
scraper than a real hiring freeze across every company at once, so we
alert about that too — separately from job matches.
"""

import hashlib
import json
import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"
LATEST_JOBS_PATH = ROOT / "data" / "latest_jobs.json"
SEEN_PATH = ROOT / "data" / "seen_jobs.json"
BASELINE_PATH = ROOT / "data" / "volume_baseline.json"

SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")


def load_json(path, default):
    if not Path(path).exists():
        return default
    with open(path) as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def job_key(job):
    """Stable composite key so we don't double-alert if an ATS reuses or
    reassigns an internal job ID. Company + title + location is stable
    across the kind of ID churn we've seen ATS platforms do."""
    raw = f"{job['company']}|{job['platform']}|{job['title']}|{job['location']}".lower().strip()
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def matches_filters(job, config):
    title = job.get("title", "").lower()
    location = job.get("location", "").lower()
    kw = config["keywords"]
    loc = config["locations"]

    include_hits = any(term in title for term in kw["title_include_any"])
    if not include_hits:
        return False

    exclude_hits = any(term in title for term in kw["title_exclude_any"])
    if exclude_hits:
        return False

    if kw.get("require_internship_or_entry"):
        if not any(term in title for term in kw["internship_terms"]):
            return False

    if loc["include_any"]:
        if not any(term in location for term in loc["include_any"]):
            return False

    return True


def send_slack_alert(new_matches):
    if not SLACK_WEBHOOK_URL:
        print("SLACK_WEBHOOK_URL not set — skipping Slack send, printing instead:", file=sys.stderr)
        for job in new_matches:
            print(f"  - {job['title']} @ {job['company']} ({job['location']}) {job['url']}")
        return

    lines = [f"*{len(new_matches)} new matching posting(s):*"]
    for job in new_matches[:20]:  # cap message size
        lines.append(f"• <{job['url']}|{job['title']}> — {job['company']} ({job['location']})")
    if len(new_matches) > 20:
        lines.append(f"...and {len(new_matches) - 20} more.")

    payload = {"text": "\n".join(lines)}
    resp = requests.post(SLACK_WEBHOOK_URL, json=payload, timeout=10)
    resp.raise_for_status()


def send_slack_warning(text):
    if not SLACK_WEBHOOK_URL:
        print(f"[WARNING] {text}", file=sys.stderr)
        return
    payload = {"text": f":warning: {text}"}
    try:
        requests.post(SLACK_WEBHOOK_URL, json=payload, timeout=10)
    except requests.RequestException:
        pass  # Don't let a failed warning-send crash the run


def check_anomaly(platform_counts, config):
    """If total volume drops far below the rolling baseline, the scraper
    likely broke (site structure change, mass rate-limiting, etc.) rather
    than every company simultaneously freezing hiring. Flag it."""
    baseline = load_json(BASELINE_PATH, {"history": []})
    total = sum(platform_counts.values())
    history = baseline["history"]

    if history:
        avg = sum(history) / len(history)
        drop_fraction = config["run"].get("anomaly_drop_fraction", 0.5)
        if avg > 0 and total < avg * (1 - drop_fraction):
            send_slack_warning(
                f"Job volume dropped sharply: {total} jobs this run vs "
                f"~{avg:.0f} average over last {len(history)} runs. "
                f"Pipeline may be broken (check for API/schema changes)."
            )

    history.append(total)
    baseline["history"] = history[-30:]  # keep a rolling window, don't grow forever
    save_json(BASELINE_PATH, baseline)


def main():
    config = load_json(CONFIG_PATH, {})
    latest = load_json(LATEST_JOBS_PATH, {"jobs": [], "platform_counts": {}, "errors": []})
    seen = load_json(SEEN_PATH, {})

    jobs = latest["jobs"]
    matches = [j for j in jobs if matches_filters(j, config)]

    new_matches = []
    for job in matches:
        key = job_key(job)
        if key not in seen:
            new_matches.append(job)
            seen[key] = job.get("updated_at", "")

    if new_matches:
        send_slack_alert(new_matches)
        print(f"Sent alert for {len(new_matches)} new matching job(s).")
    else:
        print("No new matching jobs this run.")

    save_json(SEEN_PATH, seen)
    check_anomaly(latest.get("platform_counts", {}), config)

    if latest.get("errors"):
        print(f"Note: {len(latest['errors'])} companies failed to fetch this run.", file=sys.stderr)


if __name__ == "__main__":
    main()
