import json
import time
import random
import os
import sys
from datetime import datetime
from playwright.sync_api import sync_playwright
import requests # For sending the Discord notification
import smtplib
from email.message import EmailMessage


# --- CONFIGURATION ---
# 1. Go to Discord Server Settings -> Integrations -> Webhooks -> New Webhook -> Copy Webhook URL
WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

if not WEBHOOK_URL:
    print("[!] Error: DISCORD_WEBHOOK_URL not set.")
    sys.exit(1)

# 2. Define the Microsoft Jobs URL with your specific filters.
# I have pre-filled this for "Software Engineer" in the "United States". 
# IMPORTANT: Adjust the 'lc' (Location) or 'q' (Query) as needed.
TARGET_URL = "https://apply.careers.microsoft.com/careers?domain=microsoft.com&hl=en&start=0&location=United+States&pid=1970393556659104&sort_by=timestamp&filter_include_remote=1&filter_profession=software+engineering&filter_seniority=Entry%2CMid-Level"

# File to store jobs we've already seen
DB_FILE = "seen_jobs.json"

# Titles we care about (SE 1 and SE 2 usually don't have "Senior" or "Principal")
TARGET_TITLES = ["software engineer", "software engineer ii"]
# EXCLUDE_TITLES = ["senior", "principal", "manager", "lead", "intern", "ii"] # 'ii' is in target but sometimes we want to be careful. Let's refine logic below.

def send_discord_notification(job):
    """Sends a push notification to your phone via Discord."""
    data = {
        "content": f"🚨 **NEW JOB DETECTED!** 🚨\n\n**{job['title']}**\nLocation: {job['location']}\n[Apply Now]({job['url']})"
    }
    try:
        requests.post(WEBHOOK_URL, json=data)
        print(f"[-] Notification sent for {job['id']}")
    except Exception as e:
        print(f"[!] Failed to send notification: {e}")

def send_email_notification(job):
    # Credentials from Environment Variables
    EMAIL_ADDRESS = os.environ.get("EMAIL_USER")
    EMAIL_PASSWORD = os.environ.get("EMAIL_PASS") # The 16-char App Password
    
    if not EMAIL_ADDRESS or not EMAIL_PASSWORD:
        print("[!] Email credentials missing.")
        return

    msg = EmailMessage()
    msg['Subject'] = f"New Job: {job['title']}"
    msg['From'] = EMAIL_ADDRESS
    msg['To'] = EMAIL_ADDRESS # Send to yourself
    
    body = (
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

def load_seen_jobs():
    if not os.path.exists(DB_FILE):
        return []
    with open(DB_FILE, 'r') as f:
        return json.load(f)

def save_seen_jobs(jobs):
    with open(DB_FILE, 'w') as f:
        json.dump(jobs, f)

def is_target_role(title):
    """Filters for SE 1 and SE 2 roles only."""
    t = title.lower()
    
    # 1. Must contain at least one target keyword
    if not any(target in t for target in TARGET_TITLES):
        return False
        
    # 2. Must NOT contain excluded keywords (filter out Senior/Principal)
    # Note: We allowed 'software engineer ii' in targets, so we shouldn't exclude 'ii' generally, 
    # but we should exclude 'senior'.
    exclude_list = ["senior", "principal", "manager", "staff", "partner", "director"]
    if any(exclude in t for exclude in exclude_list):
        return False
        
    return True

def run_scraper():
    seen_jobs = load_seen_jobs()
    print(f"[*] Loaded {len(seen_jobs)} previously seen jobs.")

    with sync_playwright() as p:
        # Launch browser (headless=True for background, False to see it working)
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        print(f"[*] Checking Microsoft Careers: {datetime.now().strftime('%H:%M:%S')}")
        
        try:
            page.goto(TARGET_URL, timeout=60000)
            
            # Wait for the job list to load. 
            # Note: Selectors might change. Inspect the page to verify '.job-item' or similar class exists.
            # Currently Microsoft uses aria-label or specific divs. We will wait for a generic list item container.
            # page.wait_for_selector('div[class*="job-item"]', timeout=15000)
            page.wait_for_selector('div[data-test-id="job-listing"]', timeout=15000)
            
            # Extract all job cards
            job_cards = page.locator('div[data-test-id="job-listing"]').all()
            print(f"[-] Detected {len(job_cards)} job cards on the page.\n")
            new_jobs_found = 0
            for card in job_cards[:10]:
                try:
                    # Extract Data (Selectors need to be precise based on current DOM)

                    # ink_el = card.locator('a').first
                    # raw_text = link_el.inner_text()
                    # lines = [line.strip() for line in raw_text.split('\n') if line.strip()]
                    

                    # title_el = card.locator('h2')
                    # location_el = card.locator('div[class*="job-location"]') # Approximate selector
                    
                    # if not title_el.count(): continue
                    
                    # title = title_el.inner_text().strip()
                    # Microsoft job URLs usually have the ID at the end or in a data attribute
                    # We will grab the href from the anchor tag inside the card
                    link_el = card.locator('a').first
                    raw_text = link_el.inner_text()
                    relative_link = link_el.get_attribute('href')
                    full_link = f"https://apply.careers.microsoft.com/careers?start=0&pid={relative_link}"

                    lines = [line.strip() for line in raw_text.split('\n') if line.strip()]
                    title = lines[0] if lines else 'N/A'

                    # Use the link as the unique ID
                    job_id = relative_link

                    # FILTER LOGIC
                    if job_id not in seen_jobs:
                        # New Match!
                        job_data = {
                            "id": job_id,
                            "title": title,
                            "location": "United States", # dynamic extraction is better if possible
                            "url": full_link
                        }
                            
                        # send_discord_notification(job_data)
                        send_email_notification(job_data)
                        seen_jobs.append(job_id)
                        new_jobs_found += 1
                            
                except Exception as e:
                    print(f"[!] Error parsing a card: {e}")
                    continue

            if new_jobs_found > 0:
                print(f"[*] Found {new_jobs_found} new jobs. Database updated.")
                save_seen_jobs(seen_jobs)
            else:
                print("[*] No new relevant jobs found.")

        except Exception as e:
            print(f"[!] Error during scrape: {e}")
        
        browser.close()

if __name__ == "__main__":
    # while True:
    run_scraper()
        # # Sleep for 5 to 10 minutes (randomized to look human)
        # sleep_time = random.randint(300, 600)
        # print(f"[*] Sleeping for {sleep_time} seconds...")

        # time.sleep(sleep_time)

