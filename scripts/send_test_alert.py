"""
Sends one fake job match through the real Slack alert formatting code path,
so you can see exactly what a genuine match notification looks like without
waiting for a real one. Doesn't touch latest_jobs.json, seen_jobs.json, or
any other pipeline state -- purely a formatting preview.
"""

from filter_and_alert import send_slack_alert

FAKE_JOB = {
    "title": "Machine Learning Engineering Intern",
    "company": "example-co",
    "location": "San Francisco, CA",
    "url": "https://example.com/jobs/12345",
}

if __name__ == "__main__":
    send_slack_alert([FAKE_JOB])
    print("Test alert sent (or printed above if SLACK_WEBHOOK_URL isn't set).")
