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

SEARCH_URLS = {
    "software_engineer": "https://jobs.careers.microsoft.com/global/en/search?q=software%20engineer",
    "data_engineer": "https://jobs.careers.microsoft.com/global/en/search?q=data%20engineer",
    "data_scientist": "https://jobs.careers.microsoft.com/global/en/search?q=data%20scientist"
}

DB_FILE = "seen_jobs.json"

TARGET_TITLES = ["software engineer", "data engineer", "data scientist"]
EXCLUDE_TITLES = ["senior", "principal", "manager", "staff", "partner", "director", "intern"]

# ---------------- NOTIFICATIONS ---------------- #

def send_discord_notification(job):
    data = {
        "content": f"🚨 **NEW MICROSOFT JOB!** 🚨\n\n"
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
        print("[!] Email credentials missing.")
        return

    msg = EmailMessage()
    msg['Subject'] = f"New Microsoft Job: {job['title']}"
    msg['From'] = EMAIL_ADDRESS
    msg['To'] = EMAIL_ADDRESS

    body = (
        f"Role: {job['role']}\n"
        f"Title: {job['title']}\n"
        f"Location: {job['location']}\n"
        f"Apply Here: {job['url']}"
    )
    msg.set_content(body)

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            smtp.send_message(msg)
        print(f"[-] Email sent for {job['id']}")
    except Exception as e:
        print(f"[!] Email failed: {e}")

# ---------------- STORAGE ---------------- #

def load_seen_jobs():
    if not os.path.exists(DB_FILE):
        return []
    with open(DB_FILE, 'r') as f:
        return json.load(f)

def save_seen_jobs(jobs):
    with open(DB_FILE, 'w') as f:
        json.dump(jobs, f, indent=2)

# ---------------- FILTER ---------------- #

def is_target_role(title):
    t = title.lower()

    if not any(role in t for role in TARGET_TITLES):
        return False

    if any(bad in t for bad in EXCLUDE_TITLES):
        return False

    return True

# ---------------- SCRAPER ---------------- #

def run_scraper():
    seen_jobs = load_seen_jobs()
    print(f"[*] Loaded {len(seen_jobs)} seen jobs.")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        for role, url in SEARCH_URLS.items():
            print(f"\n[*] Checking {role} jobs at {datetime.now().strftime('%H:%M:%S')}")

            try:
                page.goto(url, timeout=60000)
                page.wait_for_selector('div[data-test-id="job-listing"]', timeout=15000)

                job_cards = page.locator('div[data-test-id="job-listing"]').all()
                print(f"[-] Found {len(job_cards)} job cards.")

                for card in job_cards[:15]:
                    try:
                        link_el = card.locator('a').first
                        raw_text = link_el.inner_text()
                        relative_link = link_el.get_attribute('href')

                        if not relative_link:
                            continue

                        title = raw_text.split('\n')[0].strip()
                        job_id = relative_link
                        full_link = f"https://apply.careers.microsoft.com{relative_link}"

                        if not is_target_role(title):
                            continue

                        if job_id not in seen_jobs:
                            job_data = {
                                "id": job_id,
                                "role": role.replace("_", " ").title(),
                                "title": title,
                                "location": "United States",
                                "url": full_link
                            }

                            send_discord_notification(job_data)
                            send_email_notification(job_data)

                            seen_jobs.append(job_id)
                            save_seen_jobs(seen_jobs)
                            print(f"[+] New job: {title}")

                    except Exception as e:
                        print(f"[!] Error parsing card: {e}")

            except Exception as e:
                print(f"[!] Scrape failed for {role}: {e}")

        browser.close()

# ---------------- MAIN ---------------- #

if __name__ == "__main__":
    run_scraper()
