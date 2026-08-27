import os
import time
import logging
import threading
import requests

from dotenv import load_dotenv
from flask import Flask, send_file
from playwright.sync_api import sync_playwright, TimeoutError

load_dotenv()

app = Flask(__name__)

@app.route('/')
def home():
    if os.path.exists("latest.png"):
        return send_file("latest.png", mimetype='image/png')
    return "Bot is running, but no screenshot available yet! Refresh in 30 seconds."

def run_flask():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = threading.Thread(target=run_flask)
    t.daemon = True
    t.start()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("task_alert")

TASKS_URL = os.environ.get("HANDSHAKE_TASKS_URL", "https://ai.joinhandshake.com/fellow/a1d39753-ae51-41df-8c86-2b7e73c6bd6b/tasks")
SESSION_COOKIE = os.environ.get("HANDSHAKE_SESSION_COOKIE", "")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL_SECONDS", "30"))

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

def parse_cookies(cookie_string, domain):
    cookies = []
    for chunk in cookie_string.split(';'):
        if '=' in chunk:
            name, value = chunk.strip().split('=', 1)
            cookies.append({
                'name': name,
                'value': value,
                'domain': domain,
                'path': '/'
            })
    return cookies

def send_telegram_alert() -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.error("Telegram credentials missing, skipping alert.")
        return
        
    try:
        log.info(f"Attempting to send Telegram alert to chat ID: {TELEGRAM_CHAT_ID}...")
        body = "🚨 *Handshake Dynamo Task Alert!* 🚨\n\nA new task is available on your dashboard right now. Go claim it!\n\n🔗 [Click here to go to Tasks](" + TASKS_URL + ")"
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": body,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True
        }
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            log.info("Telegram alert sent successfully!")
        else:
            log.error(f"Failed to send Telegram alert: {resp.status_code} - {resp.text}")
    except Exception as e:
        log.error(f"Failed to send Telegram alert: {e}")

def run_monitor():
    keep_alive()
    log.info("Starting Playwright Browser monitor. Polling every %ds...", POLL_INTERVAL)
    
    with sync_playwright() as p:
        log.info("Launching Chromium once...")
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-gpu"
            ]
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
            viewport={'width': 1280, 'height': 720}
        )
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        
        domain = "ai.joinhandshake.com"
        context.add_cookies(parse_cookies(SESSION_COOKIE, domain))
        
        page = context.new_page()
        page.on("console", lambda msg: log.info(f"Browser Console: {msg.text}"))
        
        had_tasks = False
        
        while True:
            try:
                log.info("--- Starting new check cycle ---")
                page.goto(TASKS_URL, timeout=45000, wait_until="networkidle")
                
                # Wait up to 15 seconds for React to finish skeleton loaders and show the tabs
                try:
                    page.wait_for_selector("text='Available tasks'", timeout=15000)
                except:
                    log.warning("Tabs didn't load in time. Might be stuck on skeleton loader.")
                
                # Dismiss the 'Got it' tooltip if it exists
                try:
                    page.locator("text='Got it'").first.evaluate("node => node.click()", timeout=2000)
                except:
                    pass
                
                # Click the 'Available tasks' tab
                try:
                    page.locator("text='Available tasks'").last.evaluate("node => node.click()", timeout=5000)
                    log.info("Clicked 'Available tasks' tab.")
                except Exception as e:
                    log.warning(f"Could not click 'Available tasks' tab: {e}")
                
                # Wait for new tab to render
                page.wait_for_timeout(8000)
                
                # Save screenshot
                try:
                    page.screenshot(path="latest.png")
                except Exception as e:
                    log.warning(f"Screenshot failed: {e}")
                
                visible_text = page.locator("body").inner_text().lower()
                
                if "sign in" in visible_text or "log in" in visible_text or "forgot password" in visible_text:
                    log.error("🚨 ALERT: Session Expired! Please update your Cookie in .env")
                elif "captcha" in visible_text or "cloudflare" in visible_text:
                    log.error("🚨 ALERT: Handshake showing Captcha! Bot is blocked.")
                else:
                    currently_has_tasks = "claim" in visible_text
                    
                    if currently_has_tasks and not had_tasks:
                        log.info("🎯 DETECTED NEW TASKS! Sending Telegram alert...")
                        send_telegram_alert()
                    elif currently_has_tasks and had_tasks:
                        log.info("Tasks are still available, waiting for you to claim them.")
                    else:
                        log.info("No available tasks right now. (Checked with Browser)")
                        
                    had_tasks = currently_has_tasks
                    
            except TimeoutError:
                log.warning("Page load timed out, will retry next cycle.")
            except Exception as e:
                log.error(f"Error during browser check: {e}")
                
            log.info(f"Sleeping for {POLL_INTERVAL} seconds...")
            time.sleep(POLL_INTERVAL)

def main():
    run_monitor()

if __name__ == "__main__":
    main()
