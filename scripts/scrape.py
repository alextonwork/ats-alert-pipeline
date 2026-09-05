"""
Orchestrates fetching every configured company across all enabled ATS
platforms, in parallel, with per-company error isolation so one dead
or slow board never blocks the rest of the batch.

Outputs a flat list of normalized job dicts to data/latest_jobs.json,
and updates data/companies.json's dead-slug failure counters.
"""

import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from ats_clients import FETCHERS, CompanyFetchError

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"
COMPANIES_PATH = ROOT / "data" / "companies.json"
OUTPUT_PATH = ROOT / "data" / "latest_jobs.json"


def load_json(path):
    with open(path) as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def run_scrape():
    config = load_json(CONFIG_PATH)
    companies = load_json(COMPANIES_PATH)
    run_cfg = config.get("run", {})
    timeout = run_cfg.get("request_timeout_seconds", 12)
    max_retries = run_cfg.get("max_retries", 3)
    backoff_base = run_cfg.get("backoff_base_seconds", 2)
    dead_threshold = run_cfg.get("dead_slug_failure_threshold", 10)

    dead_slugs = companies.setdefault("_meta", {}).setdefault("dead_slugs", {})

    all_jobs = []
    errors = []
    platform_counts = {}

    tasks = []
    for platform, platform_cfg in config.get("platforms", {}).items():
        if not platform_cfg.get("enabled"):
            continue
        fetcher = FETCHERS.get(platform)
        if fetcher is None:
            continue
        slugs = companies.get(platform, [])
        max_workers = platform_cfg.get("max_workers", 10)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_slug = {
                executor.submit(fetcher, slug, timeout, max_retries, backoff_base): slug
                for slug in slugs
            }
            for future in as_completed(future_to_slug):
                slug = future_to_slug[future]
                key = f"{platform}:{slug}"
                try:
                    jobs = future.result()
                    all_jobs.extend(jobs)
                    platform_counts[platform] = platform_counts.get(platform, 0) + len(jobs)
                    # Reset failure count on success
                    if key in dead_slugs:
                        del dead_slugs[key]
                except CompanyFetchError as e:
                    errors.append(str(e))
                    dead_slugs[key] = dead_slugs.get(key, 0) + 1
                except Exception as e:
                    # Catch-all so one weird unexpected exception never kills the run
                    errors.append(f"[{platform}:{slug}] unexpected error: {e}")
                    dead_slugs[key] = dead_slugs.get(key, 0) + 1

    # Prune slugs that have failed too many consecutive runs — likely dead/renamed.
    # We don't delete them silently; we move them to a "removed" list so you can
    # review and re-add if it was a transient issue.
    removed = companies["_meta"].setdefault("removed_dead_slugs", [])
    for key, count in list(dead_slugs.items()):
        if count >= dead_threshold:
            platform, slug = key.split(":", 1)
            if slug in companies.get(platform, []):
                companies[platform].remove(slug)
                removed.append({"platform": platform, "slug": slug, "reason": "exceeded failure threshold"})
            del dead_slugs[key]

    # Keep full description text only for jobs that already match a topic
    # keyword — internship/level cues often live in the body, not the title,
    # but persisting every job's full description would balloon the committed
    # state file for the ~95% of jobs that are never going to match anyway.
    title_include_any = config.get("keywords", {}).get("title_include_any", [])
    for job in all_jobs:
        title = job.get("title", "").lower()
        if not any(term in title for term in title_include_any):
            job["description"] = ""

    save_json(COMPANIES_PATH, companies)
    save_json(OUTPUT_PATH, {
        "jobs": all_jobs,
        "platform_counts": platform_counts,
        "errors": errors,
    })

    print(f"Scraped {len(all_jobs)} total jobs across {sum(len(companies.get(p, [])) for p in FETCHERS)} companies.")
    print(f"Per-platform counts: {platform_counts}")
    if errors:
        print(f"{len(errors)} company fetch errors (see data/latest_jobs.json 'errors' field for detail).", file=sys.stderr)

    return all_jobs, platform_counts, errors


if __name__ == "__main__":
    run_scrape()
