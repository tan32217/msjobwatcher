import json
import time
import random
import os
import sys
from datetime import datetime
from playwright.sync_api import sync_playwright
import requests
import smtplib
from email.message import EmailMessage

# --- CONFIGURATION ---
WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

if not WEBHOOK_URL:
    print("[!] Error: DISCORD_WEBHOOK_URL not set.")
    sys.exit(1)

# UPDATED: Using the v2 URL structure seen in your screenshot
SEARCH_URLS = {
    "software_engineer": "https://careers.microsoft.com/v2/global/en/home.html?q=software%20engineer",
    "data_engineer": "https://careers.microsoft.com/v2/global/en/home.html?q=data%20engineer",
    "data_scientist": "https://careers.microsoft.com/v2/global/en/home.html?q=data%20scientist"
}

DB_FILE = "seen_jobs.json"
TARGET_TITLES = ["software engineer", "data engineer", "data scientist"]
EXCLUDE_TITLES = ["senior", "principal", "manager", "staff", "partner", "director", "intern"]

# ---------------- NOTIFICATIONS ---------------- #

def send_discord_notification(job):
    data = {
        "content": f"🚨 **NEW MICROSOFT JOB (v2)!** 🚨\n\n"
                   f"Role: {job['role']}\n"
                   f"**{job['title']}**\n"
                   f"Location: {job['location']}\n"
                   f"[Apply Now]({job['url']})"
    }
    try:
        requests.post(WEBHOOK_URL, json=data)
        print(f"[-] Discord sent for {job['id']}")
    except Exception as e:
        print(f"[!] Discord failed: {e}")

def send_email_notification(job):
    EMAIL_ADDRESS = os.environ.get("EMAIL_USER")
    EMAIL_PASSWORD = os.environ.get("EMAIL_PASS")

    if not EMAIL_ADDRESS or not EMAIL_PASSWORD:
        return

    msg = EmailMessage()
    msg['Subject'] = f"New Microsoft Job: {job['title']}"
    msg['From'] = EMAIL_ADDRESS
    msg['To'] = EMAIL_ADDRESS

    body = f"Role: {job['role']}\nTitle: {job['title']}\nLocation: {job['location']}\nApply Here: {job['url']}"
    msg.set_content(body)

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            smtp.send_message(msg)
    except Exception as e:
        print(f"[!] Email failed: {e}")

# ---------------- STORAGE ---------------- #

def load_seen_jobs():
    if not os.path.exists(DB_FILE): return []
    try:
        with open(DB_FILE, 'r') as f: return json.load(f)
    except: return []

def save_seen_jobs(jobs):
    with open(DB_FILE, 'w') as f:
        json.dump(jobs, f, indent=2)

def is_target_role(title):
    t = title.lower()
    if not any(role in t for role in TARGET_TITLES): return False
    if any(bad in t for bad in EXCLUDE_TITLES): return False
    return True

# ---------------- SCRAPER ---------------- #

def run_scraper():
    seen_jobs = load_seen_jobs()
    print(f"[*] Loaded {len(seen_jobs)} seen jobs.")

    with sync_playwright() as p:
        # Launch with a realistic User Agent to avoid bot detection
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        for role, url in SEARCH_URLS.items():
            print(f"\n[*] Checking {role} at {datetime.now().strftime('%H:%M:%S')}")

            try:
                # 1. Navigate and wait for network to settle
                page.goto(url, wait_until="networkidle", timeout=60000)
                
                # 2. Wait specifically for the job list items to appear
                # The v2 site uses 'div[role="listitem"]' for the job cards
                job_selector = 'div[role="listitem"]'
                page.wait_for_selector(job_selector, timeout=20000)
                
                # Small extra sleep to ensure text is rendered
                time.sleep(3)

                job_cards = page.locator(job_selector).all()
                print(f"[-] Found {len(job_cards)} job cards.")

                for card in job_cards[:10]:
                    try:
                        # On v2, the title is usually within an <h3> or <a> tag
                        title_el = card.locator('h3').first
                        title = title_el.inner_text().strip()
                        
                        link_el = card.locator('a[href*="/job/"]').first
                        relative_link = link_el.get_attribute('href')

                        if not relative_link: continue

                        # Clean up job ID from URL
                        job_id = relative_link.split('/')[-1]
                        full_link = f"https://careers.microsoft.com{relative_link}"

                        if not is_target_role(title): continue

                        if job_id not in seen_jobs:
                            job_data = {
                                "id": job_id,
                                "role": role.replace("_", " ").title(),
                                "title": title,
                                "location": "Check Listing",
                                "url": full_link
                            }

                            send_discord_notification(job_data)
                            send_email_notification(job_data)

                            seen_jobs.append(job_id)
                            save_seen_jobs(seen_jobs)
                            print(f"[+] New job found: {title}")

                    except Exception as e:
                        continue

            except Exception as e:
                print(f"[!] Scrape failed for {role}: {e}")
                # Save a screenshot if it fails so you can see why in GitHub Artifacts
                page.screenshot(path=f"error_{role}.png")

        browser.close()

if __name__ == "__main__":
    run_scraper()