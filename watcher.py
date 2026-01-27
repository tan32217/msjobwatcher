import json
import os
import sys
import requests
import time
from datetime import datetime
from email.message import EmailMessage
import smtplib

# --- CONFIGURATION ---
# These are pulled from your GitHub Secrets
WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
EMAIL_USER = os.environ.get("EMAIL_USER")
EMAIL_PASS = os.environ.get("EMAIL_PASS")

DB_FILE = "seen_jobs.json"

if not WEBHOOK_URL:
    print("[!] Error: DISCORD_WEBHOOK_URL not set in environment.")
    sys.exit(1)

# API Endpoint and search parameters
API_URL = "https://apply.careers.microsoft.com/api/pcsx/search"
QUERY_ROLES = ["software engineer", "data engineer", "data scientist"]
EXCLUDE_TITLES = ["senior", "principal", "manager", "staff", "partner", "director", "intern"]

# ---------------- NOTIFICATIONS ---------------- #

def send_discord_notification(job):
    data = {
        "embeds": [{
            "title": "🚨 NEW MICROSOFT JOB FOUND 🚨",
            "description": f"**{job['title']}**",
            "color": 3447003, # Blue
            "fields": [
                {"name": "Location", "value": job['location'], "inline": True},
                {"name": "Department", "value": job['department'], "inline": True},
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
        return

    msg = EmailMessage()
    msg['Subject'] = f"New Microsoft Job: {job['title']}"
    msg['From'] = EMAIL_USER
    msg['To'] = EMAIL_USER
    
    body = (
        f"Title: {job['title']}\n"
        f"Location: {job['location']}\n"
        f"Department: {job['department']}\n"
        f"Link: {job['url']}\n"
    )
    msg.set_content(body)

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(EMAIL_USER, EMAIL_PASS)
            smtp.send_message(msg)
    except Exception as e:
        print(f"[!] Email notification failed: {e}")

# ---------------- STORAGE ---------------- #

def load_seen_jobs():
    """Loads IDs of jobs already processed to avoid duplicate alerts."""
    if not os.path.exists(DB_FILE):
        return []
    try:
        with open(DB_FILE, 'r') as f:
            return json.load(f)
    except:
        return []

def save_seen_jobs(jobs):
    """Saves updated list of seen job IDs."""
    with open(DB_FILE, 'w') as f:
        json.dump(jobs, f, indent=2)

# ---------------- MAIN LOGIC ---------------- #

def run_watcher():
    seen_jobs = load_seen_jobs()
    print(f"[*] Starting scan. {len(seen_jobs)} jobs previously seen.")
    
    new_found = 0

    for query in QUERY_ROLES:
        print(f"[*] Querying API for: {query}...")
        
        # Parameters based on the API endpoint you discovered
        params = {
            "domain": "microsoft.com",
            "query": query,
            "location": "United States",
            "start": 0,
            "sort_by": "postedTs", # Prioritize the newest postings
            "filter_include_remote": 1
        }
        
        try:
            # Masking as a real browser helps prevent being blocked
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Referer": "https://careers.microsoft.com/"
            }
            
            response = requests.get(API_URL, params=params, headers=headers, timeout=15)
            response.raise_for_status()
            results = response.json().get("data", {}).get("positions", [])
            
            for pos in results:
                job_id = str(pos.get("id"))
                title = pos.get("name", "Unknown Title")
                
                # Filter out senior/intern roles
                title_lower = title.lower()
                if any(bad in title_lower for bad in EXCLUDE_TITLES):
                    continue
                
                # Check if we've alerted on this ID before
                if job_id not in seen_jobs:
                    job_data = {
                        "id": job_id,
                        "title": title,
                        "location": pos.get("standardizedLocations", ["N/A"])[0],
                        "department": pos.get("department", "N/A"),
                        "url": f"https://careers.microsoft.com{pos.get('positionUrl')}"
                    }
                    
                    # Trigger alerts
                    send_discord_notification(job_data)
                    send_email_notification(job_data)
                    
                    seen_jobs.append(job_id)
                    new_found += 1
            
            # Brief pause to be respectful to the API
            time.sleep(1)

        except Exception as e:
            print(f"[!] Error processing {query}: {e}")

    # Finalize state
    save_seen_jobs(seen_jobs)
    print(f"[*] Scan complete. Found {new_found} new roles at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

if __name__ == "__main__":
    run_watcher()