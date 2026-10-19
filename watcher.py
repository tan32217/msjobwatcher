import json
import os
import re
import sys
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from email.message import EmailMessage
import smtplib

# --- CONFIGURATION ---
WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
EMAIL_USER = os.environ.get("EMAIL_USER")
EMAIL_PASS = os.environ.get("EMAIL_PASS")
# Optional extra recipients. Pull out bare addresses so separators, quotes and brackets in the secret can't break the To header.
EMAIL_TO = re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", os.environ.get("EMAIL_TO") or "")

DB_FILE = "seen_jobs.json"
PACIFIC = ZoneInfo("America/Los_Angeles")

API_URL = "https://apply.careers.microsoft.com/api/pcsx/search"
QUERY_ROLES = ["software engineer", "data engineer", "data scientist"]
EXCLUDE_TITLES = [ "principal", "manager", "staff", "partner", "director", "intern"]
# Dedupe is done via seen_jobs.json; this window only has to outlast GitHub cron gaps (observed up to ~10h).
RECENT_HOURS = 24
MAX_PAGES = 10  # API returns 10 results per page

# API intermittently answers 429; back off and retry (honors Retry-After).
session = requests.Session()
session.mount("https://", HTTPAdapter(max_retries=Retry(total=4, backoff_factor=5, status_forcelist=[429, 500, 502, 503])))

# ---------------- UTILS ---------------- #

def is_recent(posted_ts, now=None):
    """Checks if a job was posted within the last RECENT_HOURS. API gives Unix seconds, e.g. 1791931454."""
    if not posted_ts:
        return False
    now = now or datetime.now(timezone.utc)
    return now - datetime.fromtimestamp(posted_ts, timezone.utc) <= timedelta(hours=RECENT_HOURS)

def fetch_recent_positions(params, headers, seen_jobs):
    """Pages through newest-first results until reaching jobs already seen or older than RECENT_HOURS."""
    positions = []
    for start in range(0, MAX_PAGES * 10, 10):
        response = session.get(API_URL, params={**params, "start": start}, headers=headers, timeout=15)
        response.raise_for_status()
        page = response.json().get("data", {}).get("positions", [])
        positions += page
        if not page or not is_recent(page[-1].get("postedTs")) or any(str(p.get("id")) in seen_jobs for p in page):
            break
        time.sleep(1)
    return positions

# ---------------- NOTIFICATIONS ---------------- #

def send_discord_notification(job):
    if not WEBHOOK_URL:
        return
    data = {
        "embeds": [{
            "title": "🚨 NEW MICROSOFT JOB FOUND 🚨",
            "description": f"**{job['title']}**",
            "color": 3447003, # Blue
            "fields": [
                {"name": "Location", "value": job['location'], "inline": True},
                {"name": "Department", "value": job['department'], "inline": True},
                {"name": "Posted At", "value": job['posted_at'], "inline": False},
            ],
            "url": job['url']
        }]
    }
    try:
        requests.post(WEBHOOK_URL, json=data)
        print(f"[-] Discord notification sent: {job['id']}")
    except Exception as e:
        print(f"[!] Discord notification failed: {e}")

def send_email_digest(jobs):
    """Sends one email listing all new jobs. Returns False only if sending failed."""
    if not EMAIL_USER or not EMAIL_PASS:
        print("[!] EMAIL_USER/EMAIL_PASS not set, skipping email.")
        return True

    msg = EmailMessage()
    msg['Subject'] = f"{len(jobs)} new Microsoft job{'s' if len(jobs) > 1 else ''}"
    msg['From'] = EMAIL_USER
    msg['To'] = ", ".join([EMAIL_USER] + EMAIL_TO)
    
    body = "\n\n".join(
        f"{job['title']}\n{job['location']} | {job['department']} | posted {job['posted_at']}\n{job['url']}"
        for job in sorted(jobs, key=lambda j: j['posted_ts'], reverse=True)
    )
    msg.set_content(body)

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(EMAIL_USER, EMAIL_PASS)
            refused = smtp.send_message(msg)
        print(f"[-] Email with {len(jobs)} jobs sent to {1 + len(EMAIL_TO) - len(refused)} of {1 + len(EMAIL_TO)} recipients")
        if refused:
            print(f"[!] Gmail refused: {list(refused)}")
        return True
    except Exception as e:
        print(f"[!] Email notification failed: {e}")
        return False

# ---------------- STORAGE ---------------- #

def load_seen_jobs():
    if not os.path.exists(DB_FILE):
        return []
    try:
        with open(DB_FILE, 'r') as f:
            return json.load(f)
    except:
        return []

def save_seen_jobs(jobs):
    with open(DB_FILE, 'w') as f:
        json.dump(jobs, f, indent=2)

# ---------------- MAIN LOGIC ---------------- #

def run_watcher():
    seen_jobs = load_seen_jobs()
    print(f"[*] Starting scan. {len(seen_jobs)} jobs previously seen. Filter: Last {RECENT_HOURS} hours.")
    
    new_jobs = []

    for query in QUERY_ROLES:
        print(f"[*] Querying API for: {query}...")
        
        params = {
            "domain": "microsoft.com",
            "query": query,
            "location": "United States",
            "start": 0,
            "sort_by": "timestamp",  # newest first; "postedTs" is silently ignored (falls back to relevance)
            "filter_include_remote": 1
        }
        
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Referer": "https://careers.microsoft.com/"
            }
            
            results = fetch_recent_positions(params, headers, seen_jobs)
            
            for pos in results:
                job_id = str(pos.get("id"))
                title = pos.get("name", "Unknown Title")
                posted_at = pos.get("postedTs") # Unix seconds
                
                # 1. Filter out senior/intern roles
                title_lower = title.lower()
                if any(bad in title_lower for bad in EXCLUDE_TITLES):
                    continue
                
                # 2. Search is fuzzy ("software engineer" also returns "Hardware Engineer"); require the role in the title
                if not any(role in title_lower for role in QUERY_ROLES):
                    continue
                
                # 3. Filter by Time (Posted within RECENT_HOURS)
                if not is_recent(posted_at):
                    continue
                
                # 4. Check for duplicates
                if job_id not in seen_jobs:
                    job_data = {
                        "id": job_id,
                        "title": title,
                        "location": (pos.get("standardizedLocations") or ["N/A"])[0],
                        "department": pos.get("department", "N/A"),
                        "url": f"https://apply.careers.microsoft.com{pos.get('positionUrl')}",
                        "posted_ts": posted_at,
                        "posted_at": datetime.fromtimestamp(posted_at, PACIFIC).strftime("%b %d, %I:%M %p %Z")
                    }
                    
                    send_discord_notification(job_data)
                    new_jobs.append(job_data)
                    seen_jobs.append(job_id)
            
            time.sleep(1)

        except Exception as e:
            print(f"[!] Error processing {query}: {e}")

    print(f"[*] Scan complete. Found {len(new_jobs)} new roles at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if new_jobs and not send_email_digest(new_jobs):
        sys.exit(1)  # fail the run so GitHub alerts you; jobs stay unseen and the next run retries
    save_seen_jobs(seen_jobs)

if __name__ == "__main__":
    run_watcher()