"""
Sterling Farms Tee Time Booker
================================
Logs in, waits until 5:00 AM ET on release day, then books the earliest
available tee time between 8–10 AM for 4 golfers.

Flow after selecting a time slot:
  1. Click available time link
  2. Confirmation Page 1  → click confirm button
  3. Confirmation Page 2  → click confirm button
  4. Check for success text

On site outage: retries every 30 seconds for up to 5 minutes.
On any failure: saves a screenshot and sends failure SMS.

Setup:
  pip install playwright twilio python-dotenv pytz
  playwright install chromium
"""

import os
import time
import logging
from datetime import datetime, timedelta
from pathlib import Path

import pytz
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from twilio.rest import Client as TwilioClient

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
load_dotenv()

BOOKING_URL    = "https://sterling.chelseareservations.com/mobile/golf/BookingNewm.aspx"
LOGIN_URL      = "https://sterling.chelseareservations.com/mobile/golf/LoginM.aspx"

USERNAME       = os.environ["STERLING_USERNAME"]
PASSWORD       = os.environ["STERLING_PASSWORD"]

TWILIO_SID     = os.environ["TWILIO_ACCOUNT_SID"]
TWILIO_TOKEN   = os.environ["TWILIO_AUTH_TOKEN"]
TWILIO_FROM    = os.environ["TWILIO_FROM_NUMBER"]   # e.g. +12035550100
ALERT_TO       = os.environ["ALERT_PHONE_NUMBER"]   # e.g. +19175550100

EASTERN        = pytz.timezone("America/New_York")
RELEASE_HOUR   = 5       # 5:00 AM ET
RELEASE_MIN    = 0
DAYS_AHEAD     = 7

TARGET_HOLES   = "18 Holes"
TARGET_GOLFERS = "4 Golfers"

PREFERRED_TIMES = [
    "08:00 am", "08:10 am", "08:20 am", "08:30 am", "08:40 am", "08:50 am",
    "09:00 am", "09:10 am", "09:20 am", "09:30 am", "09:40 am", "09:50 am",
    "10:00 am",
]

# Retry settings if site is unresponsive at release time
SITE_RETRY_INTERVAL_SECS = 2
SITE_RETRY_MAX_SECS      = 300   # give up after 5 minutes

# Selectors for the two confirmation pages.
# These match common Chelsea Reservations button patterns.
# Update if the site uses different labels.
CONFIRM_SELECTORS = (
    "input[value*='Confirm'], input[value*='confirm'], "
    "input[value*='Book'], input[value*='book'], "
    "input[value*='Submit'], input[value*='submit'], "
    "button:has-text('Confirm'), button:has-text('Book'), "
    "button:has-text('Submit'), button:has-text('Reserve'), "
    "a:has-text('Confirm'), a:has-text('Book'), a:has-text('Reserve')"
)

SUCCESS_KEYWORDS = ["confirmation", "confirmed", "booked", "thank you", "reservation complete"]

SCREENSHOT_DIR = Path("screenshots")
SCREENSHOT_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def send_sms(message: str) -> None:
    try:
        client = TwilioClient(TWILIO_SID, TWILIO_TOKEN)
        client.messages.create(body=message, from_=TWILIO_FROM, to=ALERT_TO)
        log.info("SMS sent: %s", message)
    except Exception as exc:
        log.error("SMS failed: %s", exc)


def screenshot(page, label: str) -> str:
    """Save a screenshot and return the file path."""
    ts = datetime.now().strftime("%H%M%S")
    path = str(SCREENSHOT_DIR / f"{ts}_{label}.png")
    try:
        page.screenshot(path=path, full_page=True)
        log.info("Screenshot saved: %s", path)
    except Exception as exc:
        log.warning("Could not save screenshot: %s", exc)
    return path


def wait_until_release() -> None:
    """Block until 5:00 AM ET."""
    now_et = datetime.now(EASTERN)
    release_today = now_et.replace(
        hour=RELEASE_HOUR, minute=RELEASE_MIN, second=0, microsecond=0
    )
    if now_et < release_today:
        wait_secs = (release_today - now_et).total_seconds()
        log.info("Waiting %.0f s until release at %s ET …", wait_secs, release_today.strftime("%H:%M:%S"))
        time.sleep(max(0, wait_secs - 5))
        while datetime.now(EASTERN) < release_today:
            time.sleep(0.1)
    else:
        log.info("Already past release time — proceeding immediately.")


def target_play_date() -> str:
    now_et  = datetime.now(EASTERN)
    play_dt = now_et + timedelta(days=DAYS_AHEAD)
    return play_dt.strftime("%B %d, %Y - ") + play_dt.strftime("%a")


def click_agree(page) -> bool:
    """
    Confirmation Page 1: click the 'I Agree' checkbox or button.
    This navigates to the next confirm page on its own.
    """
    label = "Confirm Page 1 (I Agree)"
    screenshot(page, "confirm_page1_agree")
    try:
        # Try checkbox
        agree_selector = (
            "input[type=\'checkbox\'][name*=\'gree\'], "
            "input[type=\'checkbox\'][id*=\'gree\'], "
            "input[type=\'checkbox\'][name*=\'Accept\'], "
            "input[type=\'checkbox\'][id*=\'Accept\'], "
            "input[type=\'checkbox\'][name*=\'Terms\'], "
            "input[type=\'checkbox\'][id*=\'Terms\'], "
            "label:has-text(\'I Agree\') input[type=\'checkbox\'], "
            "label:has-text(\'agree\') input[type=\'checkbox\']"
        )
        agree_box = page.locator(agree_selector)
        if agree_box.count() > 0:
            if not agree_box.first.is_checked():
                log.info("%s: checking checkbox …", label)
                agree_box.first.check()
            page.wait_for_load_state("networkidle")
            screenshot(page, "confirm_page1_after_agree")
            return True

        # Fallback: "I Agree" button or link
        agree_btn = page.locator(
            "a:has-text(\'I Agree\'), button:has-text(\'I Agree\'), "
            "input[value*=\'Agree\'], input[value*=\'agree\']"
        )
        if agree_btn.count() > 0:
            log.info("%s: clicking 'I Agree' button …", label)
            agree_btn.first.click()
            page.wait_for_load_state("networkidle")
            screenshot(page, "confirm_page1_after_agree")
            return True

        log.warning("%s: no 'I Agree' element found.", label)
        screenshot(page, "confirm_page1_no_agree")
        return False

    except PlaywrightTimeoutError:
        log.warning("%s: timeout.", label)
        screenshot(page, "confirm_page1_timeout")
        return False


def click_confirm(page, step_label: str, timeout_ms: int = 8_000) -> bool:
    """
    Click the confirm/submit button on a confirmation page.
    Used for Confirm Page 2 and Confirm Page 3.

    timeout_ms controls how long to wait for the page to settle after clicking.
    On Confirm Page 3 the site may hang indefinitely if the slot is gone — the
    short timeout ensures we bail out and try the next time slot quickly.
    """
    screenshot(page, step_label.replace(" ", "_").lower())
    try:
        btn = page.locator(CONFIRM_SELECTORS)
        if btn.count() > 0:
            log.info("%s: clicking confirm button (timeout=%dms) …", step_label, timeout_ms)
            btn.first.click(timeout=timeout_ms)
            try:
                page.wait_for_load_state("networkidle", timeout=timeout_ms)
            except PlaywrightTimeoutError:
                # Page didn't settle — likely hung because the slot is gone.
                log.warning("%s: page hung after click (slot likely taken). Aborting.", step_label)
                screenshot(page, f"{step_label.replace(' ', '_').lower()}_hung")
                return False
            return True
        else:
            log.warning("%s: no confirm button found.", step_label)
            screenshot(page, f"{step_label.replace(' ', '_').lower()}_no_button")
            return False
    except PlaywrightTimeoutError:
        log.warning("%s: timeout clicking button (slot likely taken). Aborting.", step_label)
        screenshot(page, f"{step_label.replace(' ', '_').lower()}_timeout")
        return False


# ---------------------------------------------------------------------------
# Login (with retry on site unresponsiveness)
# ---------------------------------------------------------------------------

def login(page) -> bool:
    """Navigate to login and submit credentials. Returns True on success."""
    deadline = time.time() + SITE_RETRY_MAX_SECS
    attempt  = 0

    while time.time() < deadline:
        attempt += 1
        try:
            log.info("Login attempt %d …", attempt)
            page.goto(LOGIN_URL, wait_until="networkidle", timeout=20_000)

            page.fill(
                "input[name*='Username'], input[id*='Username'], input[type='text']",
                USERNAME,
            )
            page.fill(
                "input[name*='Password'], input[id*='Password'], input[type='password']",
                PASSWORD,
            )
            page.click("input[type='submit'], button[type='submit']")
            page.wait_for_load_state("networkidle", timeout=20_000)

            if "login" not in page.url.lower():
                log.info("Logged in successfully.")
                return True

            log.warning("Still on login page after submit — credentials issue or slow load.")
            screenshot(page, "login_failed")
            return False   # wrong credentials won't improve with retries

        except PlaywrightTimeoutError:
            log.warning("Site unresponsive on attempt %d. Retrying in %ds …", attempt, SITE_RETRY_INTERVAL_SECS)
            screenshot(page, f"login_timeout_attempt{attempt}")
            time.sleep(SITE_RETRY_INTERVAL_SECS)

    log.error("Site did not respond within %d seconds. Giving up.", SITE_RETRY_MAX_SECS)
    return False


# ---------------------------------------------------------------------------
# Main booking logic
# ---------------------------------------------------------------------------

def book_tee_time() -> tuple[bool, str | None]:
    play_date = target_play_date()
    log.info("Target play date: %s", play_date)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 390, "height": 844},
            user_agent=(
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
            ),
        )
        page = context.new_page()

        # ------------------------------------------------------------------
        # Step 1: Login
        # ------------------------------------------------------------------
        if not login(page):
            browser.close()
            return False, None

        # ------------------------------------------------------------------
        # Step 2: Load booking page
        # ------------------------------------------------------------------
        page.goto(BOOKING_URL, wait_until="networkidle")
        screenshot(page, "booking_form")

        # ------------------------------------------------------------------
        # Step 3: Fill the search form
        # ------------------------------------------------------------------
        try:
            page.select_option("select[name*='Date'], select[id*='Date']", label=play_date)
        except Exception:
            play_dt = datetime.now(EASTERN) + timedelta(days=DAYS_AHEAD)
            page.select_option(
                "select[name*='Date'], select[id*='Date']",
                label=play_dt.strftime("%B %d, %Y")[:10],
            )
        page.wait_for_load_state("networkidle")

        page.select_option("select[name*='Hole'], select[id*='Hole']", label=TARGET_HOLES)
        page.wait_for_load_state("networkidle")

        page.select_option(
            "select[name*='Golfer'], select[id*='Golfer'], select[name*='Player'], select[id*='Player']",
            label=TARGET_GOLFERS,
        )
        page.wait_for_load_state("networkidle")

        # ------------------------------------------------------------------
        # Step 4: Try each preferred time
        # ------------------------------------------------------------------
        booked_time = None

        for tee_time in PREFERRED_TIMES:
            log.info("Trying %s …", tee_time)
            try:
                # Select time in dropdown
                page.select_option(
                    "select[name*='Time'], select[id*='Time'], select[name*='StartingTime']",
                    label=tee_time,
                )
                page.wait_for_load_state("networkidle")

                # Submit availability search
                page.click(
                    "input[type='submit'], button[type='submit'], "
                    "a:has-text('Search'), a:has-text('Find'), a:has-text('Go')"
                )
                page.wait_for_load_state("networkidle")
                screenshot(page, f"results_{tee_time.replace(':', '').replace(' ', '_')}")

                # Look for an available time slot link in results
                slot_selector = (
                    f"a:has-text('{tee_time}'), "
                    f"td:has-text('{tee_time}') a, "
                    f"tr:has-text('{tee_time}') a"
                )
                slot = page.locator(slot_selector)

                if slot.count() == 0:
                    log.info("%s — no slot link found (unavailable or not yet released).", tee_time)
                    page.goto(BOOKING_URL, wait_until="networkidle")
                    continue

                # Click the time slot
                log.info("%s — slot found, clicking …", tee_time)
                slot.first.click()
                page.wait_for_load_state("networkidle")

                # ------------------------------------------------------
                # Confirm Page 1: I Agree  → advances to Confirm Page 2
                # ------------------------------------------------------
                if not click_agree(page):
                    log.warning("Could not complete 'I Agree' for %s — skipping.", tee_time)
                    page.goto(BOOKING_URL, wait_until="networkidle")
                    continue

                # ------------------------------------------------------
                # Confirm Page 2: Confirm button → advances to Confirm Page 3
                # ------------------------------------------------------
                if not click_confirm(page, "Confirm Page 2"):
                    log.warning("Could not complete Confirm Page 2 for %s — skipping.", tee_time)
                    page.goto(BOOKING_URL, wait_until="networkidle")
                    continue

                # ------------------------------------------------------
                # Confirm Page 3: final Confirm button → success page
                # A hung response here means the slot was taken — bail fast.
                # ------------------------------------------------------
                if not click_confirm(page, "Confirm Page 3", timeout_ms=2_000):
                    log.warning("Could not complete Confirm Page 3 for %s — skipping.", tee_time)
                    page.goto(BOOKING_URL, wait_until="networkidle")
                    continue

                # ------------------------------------------------------
                # Check for success
                # ------------------------------------------------------
                page_content = page.content().lower()
                screenshot(page, "final_page")

                if any(kw in page_content for kw in SUCCESS_KEYWORDS):
                    booked_time = tee_time
                    log.info("✅ Booking confirmed for %s!", tee_time)
                    break
                else:
                    log.warning("Completed all 3 confirm steps for %s but no success text found.", tee_time)
                    page.goto(BOOKING_URL, wait_until="networkidle")

            except PlaywrightTimeoutError:
                log.warning("Timeout while processing %s — skipping.", tee_time)
                screenshot(page, f"timeout_{tee_time.replace(':', '').replace(' ', '_')}")
                try:
                    page.goto(BOOKING_URL, wait_until="networkidle")
                except Exception:
                    pass

            except Exception as exc:
                log.error("Unexpected error on %s: %s", tee_time, exc)
                screenshot(page, f"error_{tee_time.replace(':', '').replace(' ', '_')}")
                try:
                    page.goto(BOOKING_URL, wait_until="networkidle")
                except Exception:
                    pass

        browser.close()

    return (booked_time is not None), booked_time


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    play_date_str = target_play_date()
    log.info("=== Sterling Farms Tee Time Booker ===")
    log.info("Play date target : %s", play_date_str)
    log.info("Preferred times  : %s", ", ".join(PREFERRED_TIMES))

    wait_until_release()

    log.info("Release time reached — starting booking attempt …")
    send_sms(f"⛳ Tee time bot started! Attempting to book Sterling Farms on {play_date_str} …")

    success, booked = book_tee_time()

    if success:
        send_sms(
            f"✅ TEE TIME BOOKED! Sterling Farms · {play_date_str} · {booked} · 4 players. "
            f"Check your email for confirmation."
        )
    else:
        send_sms(
            f"❌ Booking FAILED for Sterling Farms on {play_date_str}. "
            f"No slots booked between 8–10 AM. Book manually NOW: {BOOKING_URL}"
        )
        log.error("Booking failed — check screenshots/ folder for details.")
