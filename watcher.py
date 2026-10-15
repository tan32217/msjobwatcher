import json
import os
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
import smtplib

# --- CONFIGURATION ---
WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
EMAIL_USER = os.environ.get("EMAIL_USER")
EMAIL_PASS = os.environ.get("EMAIL_PASS")
EMAIL_TO = os.environ.get("EMAIL_TO")  # optional extra recipients, comma-separated

DB_FILE = "seen_jobs.json"

API_URL = "https://apply.careers.microsoft.com/api/pcsx/search"
QUERY_ROLES = ["software engineer", "data engineer", "data scientist"]
EXCLUDE_TITLES = [ "principal", "manager", "staff", "partner", "director", "intern"]
# Dedupe is done via seen_jobs.json; this window only caps the first run and absorbs GitHub cron delays.
RECENT_HOURS = 3

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

def send_email_notification(job):
    if not EMAIL_USER or not EMAIL_PASS:
        print("[!] EMAIL_USER/EMAIL_PASS not set, skipping email.")
        return

    msg = EmailMessage()
    msg['Subject'] = f"New Microsoft Job: {job['title']}"
    msg['From'] = EMAIL_USER
    msg['To'] = EMAIL_USER
    if EMAIL_TO:
        msg['Bcc'] = EMAIL_TO  # Bcc so recipients don't see each other's addresses
    
    body = (
        f"Title: {job['title']}\n"
        f"Location: {job['location']}\n"
        f"Department: {job['department']}\n"
        f"Posted At: {job['posted_at']}\n"
        f"Link: {job['url']}\n"
    )
    msg.set_content(body)

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(EMAIL_USER, EMAIL_PASS)
            smtp.send_message(msg)
        print(f"[-] Email sent: {job['id']}")
    except Exception as e:
        print(f"[!] Email notification failed: {e}")

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
    
    new_found = 0

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
            
            response = session.get(API_URL, params=params, headers=headers, timeout=15)
            response.raise_for_status()
            results = response.json().get("data", {}).get("positions", [])
            
            for pos in results:
                job_id = str(pos.get("id"))
                title = pos.get("name", "Unknown Title")
                posted_at = pos.get("postedTs") # Unix seconds
                
                # 1. Filter out senior/intern roles
                title_lower = title.lower()
                if any(bad in title_lower for bad in EXCLUDE_TITLES):
                    continue
                
                # 2. Filter by Time (Posted within last hour)
                if not is_recent(posted_at):
                    continue
                
                # 3. Check for duplicates
                if job_id not in seen_jobs:
                    job_data = {
                        "id": job_id,
                        "title": title,
                        "location": (pos.get("standardizedLocations") or ["N/A"])[0],
                        "department": pos.get("department", "N/A"),
                        "url": f"https://apply.careers.microsoft.com{pos.get('positionUrl')}",
                        "posted_at": datetime.fromtimestamp(posted_at, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                    }
                    
                    send_discord_notification(job_data)
                    send_email_notification(job_data)
                    
                    seen_jobs.append(job_id)
                    new_found += 1
            
            time.sleep(1)

        except Exception as e:
            print(f"[!] Error processing {query}: {e}")

    save_seen_jobs(seen_jobs)
    print(f"[*] Scan complete. Found {new_found} new roles at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

if __name__ == "__main__":
    run_watcher()