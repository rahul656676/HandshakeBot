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

def run_browser_check(previously_had_tasks: bool) -> bool:
    currently_has_tasks = previously_had_tasks
    
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-gpu",
                "--disable-background-networking",
                "--disable-background-timer-throttling",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding"
            ]
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
            viewport={'width': 1280, 'height': 720}
        )
        
        # Mask webdriver to prevent basic bot detection that might cause infinite loading
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        
        domain = "ai.joinhandshake.com"
        cookie_list = parse_cookies(SESSION_COOKIE, domain)
        context.add_cookies(cookie_list)
        
        page = context.new_page()
        
        # Intercept and print console messages from the browser to see if there are JS errors
        page.on("console", lambda msg: log.info(f"Browser Console: {msg.text}"))
        
        try:
            page.goto(TASKS_URL, timeout=45000, wait_until="networkidle")
            
            # Wait for basic layout to load
            page.wait_for_timeout(5000)
            
            # Dismiss the 'Got it' tooltip if it exists using pure JS
            try:
                page.locator("text='Got it'").first.evaluate("node => node.click()")
                log.info("Dismissed tooltip via JS.")
                page.wait_for_timeout(1000)
            except:
                pass
            
            # Click the 'Available tasks' tab using pure JS!
            try:
                page.locator("text='Available tasks'").last.evaluate("node => node.click()")
                log.info("Successfully clicked the 'Available tasks' tab via JS.")
            except Exception as e:
                log.warning(f"Could not click 'Available tasks' tab: {e}")
            
            # Wait 15 extra seconds for React to fetch and render the new tab
            page.wait_for_timeout(15000)
            
            # Save screenshot for debugging
            page.screenshot(path="latest.png")
            log.info("Screenshot saved. Check the Render URL to see what the bot sees.")
            
            visible_text = page.locator("body").inner_text().lower()
            
            if "sign in" in visible_text or "log in" in visible_text or "forgot password" in visible_text:
                log.error("🚨 ALERT: Session Expired! Please update your Cookie in .env")
                return previously_had_tasks
                
            if "captcha" in visible_text or "cloudflare" in visible_text:
                log.error("🚨 ALERT: Handshake showing Captcha! Bot is blocked.")
                return previously_had_tasks
                
            # POSITIVE CHECK: Only assume task exists if 'claim' is visible on the screen
            if "claim" in visible_text:
                currently_has_tasks = True
            else:
                currently_has_tasks = False
        except TimeoutError:
            log.warning("Page load timed out, will retry next cycle.")
            return previously_had_tasks
        except Exception as e:
            log.error(f"Error during browser check: {e}")
            return previously_had_tasks
        finally:
            browser.close()

    if currently_has_tasks and not previously_had_tasks:
        log.info("🎯 DETECTED NEW TASKS! Sending Telegram alert...")
        send_telegram_alert()
    elif currently_has_tasks and previously_had_tasks:
        log.info("Tasks are still available, waiting for you to claim them.")
    else:
        log.info("No available tasks right now. (Checked with Browser)")
    return currently_has_tasks

def main():
    keep_alive()
    log.info("Starting Playwright Browser monitor. Polling every %ds...", POLL_INTERVAL)
    had_tasks = False
    while True:
        had_tasks = run_browser_check(had_tasks)
        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()
