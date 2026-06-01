"""
Sterling Farms Tee Time Booker
================================
Targets the desktop booking page:
  https://sterling.chelseareservations.com/golf/bookingadmin.aspx

Flow:
  1. Log in
  2. Navigate to booking page
  3. Set Starting Time, # of Golfers, # of Holes dropdowns
  4. Click the target date on the calendar (uses __doPostBack)
  5. For each preferred time slot (8:00–10:00 AM in 10-min steps),
     click the available slot link in results
  6. Confirm Page 1 → click "I Agree"
  7. Confirm Page 2 → click Confirm
  8. Confirm Page 3 → click Confirm (2s timeout; hangs = slot taken)
  9. Verify success text and send email

On site outage: retries every 2 seconds for up to 5 minutes.
On any failure: saves a screenshot and sends failure email.

Setup:
  pip install playwright python-dotenv pytz
  playwright install chromium
"""

import os
import time
import logging
import smtplib
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from pathlib import Path

import pytz
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
load_dotenv()

BOOKING_URL    = "https://sterling.chelseareservations.com/golf/bookingadmin.aspx"
LOGIN_URL      = "https://sterling.chelseareservations.com/golf/Login.aspx"

USERNAME       = os.environ["STERLING_USERNAME"]
PASSWORD       = os.environ["STERLING_PASSWORD"]

GMAIL_USER     = os.environ["GMAIL_USER"]          # your Gmail address
GMAIL_APP_PASS = os.environ["GMAIL_APP_PASSWORD"]  # Gmail App Password
ALERT_EMAIL    = "andrpicardi@gmail.com"

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

# Common confirm/submit button selectors
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

def send_email(subject: str, body: str) -> None:
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"]    = GMAIL_USER
        msg["To"]      = ALERT_EMAIL
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(GMAIL_USER, GMAIL_APP_PASS)
            smtp.send_message(msg)
        log.info("Email sent to %s: %s", ALERT_EMAIL, subject)
    except Exception as exc:
        log.error("Email failed: %s", exc)


def screenshot(page, label: str) -> str:
    ts   = datetime.now().strftime("%H%M%S")
    path = str(SCREENSHOT_DIR / f"{ts}_{label}.png")
    try:
        page.screenshot(path=path, full_page=True)
        log.info("Screenshot saved: %s", path)
    except Exception as exc:
        log.warning("Could not save screenshot: %s", exc)
    return path


def wait_until_release() -> None:
    """Block until 5:00 AM ET."""
    now_et        = datetime.now(EASTERN)
    release_today = now_et.replace(hour=RELEASE_HOUR, minute=RELEASE_MIN, second=0, microsecond=0)
    if now_et < release_today:
        wait_secs = (release_today - now_et).total_seconds()
        log.info("Waiting %.0f s until release at %s ET …", wait_secs, release_today.strftime("%H:%M:%S"))
        time.sleep(max(0, wait_secs - 5))
        while datetime.now(EASTERN) < release_today:
            time.sleep(0.1)
    else:
        log.info("Already past release time — proceeding immediately.")


def target_play_date() -> datetime:
    """Return the play date as a datetime object (7 days from now ET)."""
    return datetime.now(EASTERN) + timedelta(days=DAYS_AHEAD)


# ---------------------------------------------------------------------------
# Confirmation page handlers
# ---------------------------------------------------------------------------

def click_agree(page) -> bool:
    """
    Confirm Page 1: click the 'I Agree' checkbox or button.
    Navigates to Confirm Page 2 automatically.
    """
    label = "Confirm Page 1 (I Agree)"
    screenshot(page, "confirm_page1_agree")
    try:
        agree_checkbox_selector = (
            "input[type='checkbox'][name*='gree'], "
            "input[type='checkbox'][id*='gree'], "
            "input[type='checkbox'][name*='Accept'], "
            "input[type='checkbox'][id*='Accept'], "
            "input[type='checkbox'][name*='Terms'], "
            "input[type='checkbox'][id*='Terms'], "
            "label:has-text('I Agree') input[type='checkbox'], "
            "label:has-text('agree') input[type='checkbox']"
        )
        agree_box = page.locator(agree_checkbox_selector)
        if agree_box.count() > 0:
            if not agree_box.first.is_checked():
                log.info("%s: checking checkbox …", label)
                agree_box.first.check()
            page.wait_for_load_state("networkidle")
            screenshot(page, "confirm_page1_after_agree")
            return True

        # Fallback: "I Agree" button or link
        agree_btn = page.locator(
            "a:has-text('I Agree'), button:has-text('I Agree'), "
            "input[value*='Agree'], input[value*='agree']"
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
    timeout_ms: how long to wait after clicking before giving up.
    Confirm Page 3 uses 2s — a hung response means the slot is gone.
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
    deadline = time.time() + SITE_RETRY_MAX_SECS
    attempt  = 0

    while time.time() < deadline:
        attempt += 1
        try:
            log.info("Login attempt %d …", attempt)
            page.goto(LOGIN_URL, wait_until="networkidle", timeout=20_000)

            page.fill(
                "input[name*='Username'], input[id*='Username'], input[name*='Email'], input[type='text']",
                USERNAME,
            )
            page.fill(
                "input[name*='Password'], input[id*='Password'], input[type='password']",
                PASSWORD,
            )
            page.click("input[type='submit'], button[type='submit']")
            page.wait_for_load_state("networkidle", timeout=20_000)

            if "login" not in page.url.lower() and "passwd" not in page.url.lower():
                log.info("Logged in. Current URL: %s", page.url)
                return True

            log.warning("Still on login page — check credentials. URL: %s", page.url)
            screenshot(page, "login_failed")
            return False  # bad credentials won't improve with retries

        except PlaywrightTimeoutError:
            log.warning("Site unresponsive (attempt %d). Retrying in %ds …", attempt, SITE_RETRY_INTERVAL_SECS)
            screenshot(page, f"login_timeout_attempt{attempt}")
            time.sleep(SITE_RETRY_INTERVAL_SECS)

    log.error("Site did not respond within %d seconds. Giving up.", SITE_RETRY_MAX_SECS)
    return False


# ---------------------------------------------------------------------------
# Main booking logic
# ---------------------------------------------------------------------------

def book_tee_time() -> tuple[bool, str | None]:
    play_dt   = target_play_date()
    play_day  = play_dt.day          # e.g. 7  (used for __doPostBack('Day7',''))
    play_str  = play_dt.strftime("%B %d, %Y (%A)")  # for logging/email

    log.info("Target play date: %s (day=%d)", play_str, play_day)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},   # desktop viewport
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
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
        # Step 3: Set dropdowns BEFORE clicking the calendar date
        #   The desktop form has: Starting Time, # of Golfers, # of Holes
        # ------------------------------------------------------------------
        try:
            page.select_option(
                "select[name*='Time'], select[id*='Time'], select[name*='StartingTime']",
                label="08:00 am",   # we'll iterate times after the calendar click
            )
        except Exception as e:
            log.warning("Could not pre-set starting time: %s", e)

        try:
            page.select_option(
                "select[name*='Golfer'], select[id*='Golfer'], select[name*='Player'], select[id*='Player']",
                label=TARGET_GOLFERS,
            )
        except Exception as e:
            log.warning("Could not set golfers: %s", e)

        try:
            page.select_option(
                "select[name*='Hole'], select[id*='Hole']",
                label=TARGET_HOLES,
            )
        except Exception as e:
            log.warning("Could not set holes: %s", e)

        # ------------------------------------------------------------------
        # Step 4: Click the target date on the calendar
        #   The calendar uses links like: javascript:__doPostBack('Day7','')
        #   Playwright can click the <a> whose href contains DayN
        # ------------------------------------------------------------------
        day_link_selector = f"a[href*='Day{play_day}']"
        try:
            log.info("Clicking calendar day %d …", play_day)
            page.wait_for_selector(day_link_selector, timeout=10_000)
            page.click(day_link_selector)
            page.wait_for_load_state("networkidle")
            screenshot(page, "after_date_click")
        except PlaywrightTimeoutError:
            log.error("Could not find calendar day %d on page. Check screenshot.", play_day)
            screenshot(page, "calendar_day_not_found")
            browser.close()
            return False, None

        # ------------------------------------------------------------------
        # Step 5: Iterate preferred times and try to book
        # ------------------------------------------------------------------
        booked_time = None

        for tee_time in PREFERRED_TIMES:
            log.info("Trying %s …", tee_time)
            try:
                # Update the Starting Time dropdown and re-submit
                # (some Chelsea sites reload results on dropdown change via postback)
                page.select_option(
                    "select[name*='Time'], select[id*='Time'], select[name*='StartingTime']",
                    label=tee_time,
                )
                page.wait_for_load_state("networkidle")

                # Look for a clickable slot link matching this time in the results grid
                slot_selector = (
                    f"a:has-text('{tee_time}'), "
                    f"td:has-text('{tee_time}') a, "
                    f"tr:has-text('{tee_time}') a"
                )
                slot = page.locator(slot_selector)

                if slot.count() == 0:
                    log.info("%s — no available slot found.", tee_time)
                    continue

                # Click the slot
                log.info("%s — slot found, clicking …", tee_time)
                slot.first.click()
                page.wait_for_load_state("networkidle")
                screenshot(page, f"slot_clicked_{tee_time.replace(':', '').replace(' ', '_')}")

                # ------------------------------------------------------
                # Confirm Page 1: I Agree → advances to Confirm Page 2
                # ------------------------------------------------------
                if not click_agree(page):
                    log.warning("Could not complete 'I Agree' for %s — skipping.", tee_time)
                    page.goto(BOOKING_URL, wait_until="networkidle")
                    _reselect_after_reload(page, play_day)
                    continue

                # ------------------------------------------------------
                # Confirm Page 2: Confirm button → advances to Confirm Page 3
                # ------------------------------------------------------
                if not click_confirm(page, "Confirm Page 2"):
                    log.warning("Could not complete Confirm Page 2 for %s — skipping.", tee_time)
                    page.goto(BOOKING_URL, wait_until="networkidle")
                    _reselect_after_reload(page, play_day)
                    continue

                # ------------------------------------------------------
                # Confirm Page 3: final Confirm (2s timeout; hang = slot gone)
                # ------------------------------------------------------
                if not click_confirm(page, "Confirm Page 3", timeout_ms=2_000):
                    log.warning("Confirm Page 3 hung for %s — slot likely taken. Trying next.", tee_time)
                    page.goto(BOOKING_URL, wait_until="networkidle")
                    _reselect_after_reload(page, play_day)
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
                    log.warning("All 3 confirm steps done for %s but no success text found.", tee_time)
                    page.goto(BOOKING_URL, wait_until="networkidle")
                    _reselect_after_reload(page, play_day)

            except PlaywrightTimeoutError:
                log.warning("Timeout on %s — skipping.", tee_time)
                screenshot(page, f"timeout_{tee_time.replace(':', '').replace(' ', '_')}")
                try:
                    page.goto(BOOKING_URL, wait_until="networkidle")
                    _reselect_after_reload(page, play_day)
                except Exception:
                    pass

            except Exception as exc:
                log.error("Unexpected error on %s: %s", tee_time, exc)
                screenshot(page, f"error_{tee_time.replace(':', '').replace(' ', '_')}")
                try:
                    page.goto(BOOKING_URL, wait_until="networkidle")
                    _reselect_after_reload(page, play_day)
                except Exception:
                    pass

        browser.close()

    return (booked_time is not None), booked_time


def _reselect_after_reload(page, play_day: int) -> None:
    """
    After navigating back to the booking page, re-set dropdowns and
    re-click the calendar date so results are ready for the next attempt.
    """
    try:
        page.select_option(
            "select[name*='Golfer'], select[id*='Golfer'], select[name*='Player'], select[id*='Player']",
            label=TARGET_GOLFERS,
        )
        page.select_option("select[name*='Hole'], select[id*='Hole']", label=TARGET_HOLES)
        day_link_selector = f"a[href*='Day{play_day}']"
        page.wait_for_selector(day_link_selector, timeout=8_000)
        page.click(day_link_selector)
        page.wait_for_load_state("networkidle")
    except Exception as e:
        log.warning("_reselect_after_reload failed: %s", e)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    play_dt      = target_play_date()
    play_date_str = play_dt.strftime("%B %d, %Y (%A)")

    log.info("=== Sterling Farms Tee Time Booker ===")
    log.info("Play date target : %s", play_date_str)
    log.info("Preferred times  : %s", ", ".join(PREFERRED_TIMES))

    wait_until_release()

    log.info("Release time reached — starting booking attempt …")
    send_email("⛳ Tee Time Bot Started", f"Attempting to book Sterling Farms on {play_date_str}…")

    success, booked = book_tee_time()

    if success:
        send_email(
            "✅ Tee Time Booked!",
            f"Your tee time has been booked!\n\n"
            f"Course : Sterling Farms\n"
            f"Date   : {play_date_str}\n"
            f"Time   : {booked}\n"
            f"Players: 4\n\n"
            f"Check your Sterling Farms account for the confirmation details."
        )
    else:
        send_email(
            "❌ Tee Time Booking Failed",
            f"The tee time bot could not book a slot for Sterling Farms on {play_date_str}.\n\n"
            f"No available times found between 8–10 AM.\n\n"
            f"Book manually now: {BOOKING_URL}"
        )
        log.error("Booking failed — check screenshots/ folder for details.")
