"""
Discovers new company ATS boards you're not already tracking, so you don't
have to manually hand-seed every startup you hear about.

Approach: search-engine site-search against each ATS's board domain,
extract the company slug from result URLs, validate each candidate slug
with one real API call (cheap — same fetch we'd do anyway), and only
persist slugs that actually resolve to a live board with open jobs.

This is intentionally the "cheap, good-enough" version — not a full
Common Crawl re-implementation. It runs weekly, not daily, because new
company boards don't appear fast enough to need tighter monitoring, and
because search-API usage is the part worth rationing.

Uses the Google Programmable Search Engine (Custom Search JSON API).
Free tier is 100 queries/day; this script issues 12 queries/run
(4 search terms x 3 ATS platforms), well within that. Requires two
secrets: SEARCH_API_KEY (a Custom Search JSON API key) and
SEARCH_ENGINE_ID (the search engine's "cx" value) — both come from
https://programmablesearchengine.google.com/.
"""

import json
import os
import re
import sys
from pathlib import Path

import requests

from ats_clients import FETCHERS

ROOT = Path(__file__).resolve().parent.parent
COMPANIES_PATH = ROOT / "data" / "companies.json"
CONFIG_PATH = ROOT / "config.json"

SEARCH_API_KEY = os.environ.get("SEARCH_API_KEY")  # Custom Search JSON API key
SEARCH_ENGINE_ID = os.environ.get("SEARCH_ENGINE_ID")  # the search engine's "cx" value

# ATS domain patterns and how to pull the slug out of a matched URL
BOARD_PATTERNS = {
    "greenhouse": {
        "site": "boards.greenhouse.io",
        "slug_regex": re.compile(r"boards\.greenhouse\.io/([a-zA-Z0-9_-]+)"),
    },
    "lever": {
        "site": "jobs.lever.co",
        "slug_regex": re.compile(r"jobs\.lever\.co/([a-zA-Z0-9_-]+)"),
    },
    "ashby": {
        "site": "jobs.ashbyhq.com",
        "slug_regex": re.compile(r"jobs\.ashbyhq\.com/([a-zA-Z0-9_-]+)"),
    },
}

# Search terms tuned toward what you're actually hiring-watching for,
# not a generic crawl of every company on each ATS.
SEARCH_TERMS = [
    "internship",
    "electrical engineer intern",
    "machine learning intern",
    "new grad software",
]


def load_json(path, default):
    if not Path(path).exists():
        return default
    with open(path) as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def search_web(query):
    """Google Programmable Search Engine (Custom Search JSON API). Returns a
    list of result URLs. Returns [] (not an exception) on any failure, since
    a failed search for one term shouldn't stop the whole discovery run.
    Google's API caps each request at 10 results (the `num` param) — no
    pagination here since 10 results/term is plenty for slug discovery."""
    if not SEARCH_API_KEY or not SEARCH_ENGINE_ID:
        print("SEARCH_API_KEY/SEARCH_ENGINE_ID not set — skipping web discovery this run.", file=sys.stderr)
        return []
    try:
        resp = requests.get(
            "https://www.googleapis.com/customsearch/v1",
            params={"key": SEARCH_API_KEY, "cx": SEARCH_ENGINE_ID, "q": query, "num": 10},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return [item["link"] for item in data.get("items", [])]
    except requests.RequestException as e:
        print(f"Search failed for '{query}': {e}", file=sys.stderr)
        return []


def discover_candidate_slugs():
    """Returns {platform: set(candidate_slugs)}."""
    candidates = {platform: set() for platform in BOARD_PATTERNS}
    for platform, pattern_cfg in BOARD_PATTERNS.items():
        site = pattern_cfg["site"]
        regex = pattern_cfg["slug_regex"]
        for term in SEARCH_TERMS:
            query = f"site:{site} {term}"
            urls = search_web(query)
            for url in urls:
                m = regex.search(url)
                if m:
                    candidates[platform].add(m.group(1))
    return candidates


def validate_slug(platform, slug, timeout=10):
    """One real API call to confirm the slug is a live board with jobs.
    This is the same fetch scrape.py does, so validation cost is the
    same as one normal poll — no separate heavier check needed."""
    fetcher = FETCHERS.get(platform)
    if not fetcher:
        return False
    try:
        jobs = fetcher(slug, timeout=timeout, max_retries=1, backoff_base=1)
        return len(jobs) > 0
    except Exception:
        return False


def main():
    companies = load_json(COMPANIES_PATH, {})
    added = []

    candidates = discover_candidate_slugs()
    for platform, slugs in candidates.items():
        existing = set(companies.get(platform, []))
        removed_slugs = {
            entry["slug"] for entry in companies.get("_meta", {}).get("removed_dead_slugs", [])
            if entry["platform"] == platform
        }
        new_candidates = slugs - existing - removed_slugs

        for slug in new_candidates:
            if validate_slug(platform, slug):
                companies.setdefault(platform, []).append(slug)
                added.append(f"{platform}:{slug}")

    save_json(COMPANIES_PATH, companies)

    print(f"Discovery run complete. Added {len(added)} new validated companies.")
    if added:
        print("\n".join(f"  + {a}" for a in added))


if __name__ == "__main__":
    main()
