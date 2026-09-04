"""
ATS client functions for Greenhouse, Lever, and Ashby public job-board APIs.

All three expose unauthenticated, public JSON endpoints intended for embedding
job boards on company marketing sites. No API key required. Each function
returns a normalized list of job dicts, or raises a `CompanyFetchError` that
callers can catch per-company without aborting the whole batch.
"""

import time
import requests

USER_AGENT = "ats-alert-pipeline/1.0 (personal job-search alert tool)"


class CompanyFetchError(Exception):
    """Raised when a single company's board can't be fetched, after retries.
    Callers should catch this per-company and continue the batch."""
    def __init__(self, slug, platform, reason, status_code=None):
        self.slug = slug
        self.platform = platform
        self.reason = reason
        self.status_code = status_code
        super().__init__(f"[{platform}:{slug}] {reason}")


def _get_with_retry(url, timeout, max_retries, backoff_base, slug, platform):
    """Shared GET-with-backoff logic. Retries on 429 and 5xx; gives up
    immediately on 404 (company doesn't exist / renamed / no board)."""
    last_exc = None
    for attempt in range(max_retries):
        try:
            resp = requests.get(
                url,
                timeout=timeout,
                headers={"User-Agent": USER_AGENT},
            )
        except requests.RequestException as e:
            last_exc = e
            time.sleep(backoff_base * (2 ** attempt))
            continue

        if resp.status_code == 200:
            return resp
        if resp.status_code == 404:
            raise CompanyFetchError(slug, platform, "not found (404) — slug may be dead", 404)
        if resp.status_code == 429 or resp.status_code >= 500:
            # Respect Retry-After if present, else exponential backoff
            retry_after = resp.headers.get("Retry-After")
            wait = float(retry_after) if retry_after else backoff_base * (2 ** attempt)
            time.sleep(wait)
            last_exc = CompanyFetchError(slug, platform, f"HTTP {resp.status_code}", resp.status_code)
            continue
        # Any other unexpected status: don't retry, just fail
        raise CompanyFetchError(slug, platform, f"unexpected HTTP {resp.status_code}", resp.status_code)

    raise last_exc or CompanyFetchError(slug, platform, "exhausted retries")


def fetch_greenhouse(slug, timeout=12, max_retries=3, backoff_base=2):
    """Greenhouse public Job Board API.
    Docs pattern: boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true
    """
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
    resp = _get_with_retry(url, timeout, max_retries, backoff_base, slug, "greenhouse")
    data = resp.json()
    jobs = []
    for j in data.get("jobs", []):
        jobs.append({
            "company": slug,
            "platform": "greenhouse",
            "title": j.get("title", ""),
            "location": (j.get("location") or {}).get("name", ""),
            "url": j.get("absolute_url", ""),
            "job_id": str(j.get("id", "")),
            "updated_at": j.get("updated_at", ""),
        })
    return jobs


def fetch_lever(slug, timeout=12, max_retries=3, backoff_base=2):
    """Lever public Postings API.
    Docs pattern: api.lever.co/v0/postings/{slug}?mode=json
    """
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    resp = _get_with_retry(url, timeout, max_retries, backoff_base, slug, "lever")
    data = resp.json()
    jobs = []
    for j in data:
        categories = j.get("categories", {}) or {}
        jobs.append({
            "company": slug,
            "platform": "lever",
            "title": j.get("text", ""),
            "location": categories.get("location", ""),
            "url": j.get("hostedUrl", ""),
            "job_id": str(j.get("id", "")),
            "updated_at": str(j.get("createdAt", "")),
        })
    return jobs


def fetch_ashby(slug, timeout=12, max_retries=3, backoff_base=2):
    """Ashby public Job Board API.
    Docs pattern: api.ashbyhq.com/posting-api/job-board/{slug}
    """
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
    resp = _get_with_retry(url, timeout, max_retries, backoff_base, slug, "ashby")
    data = resp.json()
    jobs = []
    for j in data.get("jobs", []):
        jobs.append({
            "company": slug,
            "platform": "ashby",
            "title": j.get("title", ""),
            "location": j.get("location", ""),
            "url": j.get("jobUrl", ""),
            "job_id": str(j.get("id", "")),
            "updated_at": j.get("publishedAt", ""),
        })
    return jobs


FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
}
