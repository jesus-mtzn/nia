#!/usr/bin/env python3
"""
record.py — 24/7 Area de Carga recorder, deployed on the VPS.

This is your record_clip.py, adapted for the new infrastructure. The
interaction logic is unchanged because it's already proven against the
real site — this was tested, not guessed:

  - Landing on /playback does NOT drop you straight into a working video +
    record button. You have to navigate: Share tab -> device group ->
    device card -> fullscreen -> THEN the record button appears.
  - The feed intermittently shows a "Failed to connect" overlay with a
    Retry link, both on initial load and mid-recording. Unhandled, an
    unattended run eventually just gets stuck on this.
  - Session expiry shows the literal text "Escanea el codigo QR" -- a
    reliable way to detect a dead session, no guessing needed.
  - The record button produces a real .mp4 download directly. No need to
    inject captureStream()/MediaRecorder JS -- the site already does the
    decoding work; we just click the button that saves the result.

What changed from the Chromebook version:
  - No more USB_DRIVE_PATH / ChromeOS mount logic -- the VPS has no
    directly-attached drive. Clips land in a local "segments" folder that
    the nightly pull_footage.sh job (run on your Mac) pulls down to the
    real external drive.
  - HEADLESS=True is no longer a "heavy on weak hardware" compromise --
    it's just how a server runs. No behavior change expected from this.
  - RECORD_SECONDS raised cautiously from the proven 60s. See the note
    below before pushing this further -- we don't yet know if 60s was a
    Chromebook limitation or a platform-side ceiling.
  - Paths are overridable via environment variables, so the exact same
    script works for a local Mac test run and the real VPS deployment.

USAGE:
    python record.py            # records ONE clip and exits
    python record.py --loop     # records clips back-to-back forever
                                 # (this is what the systemd service runs)
"""

import os
import sys
import time
import datetime
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# ---------- CONFIG ----------
SITE_URL = "https://protect-us.ismartlife.me/playback"

# Overridable so you can test locally first: e.g.
#   AUTH_STATE_FILE=./storage_state.json ARCHIVE_DIR=./segments python record.py
AUTH_STATE_FILE = os.environ.get("AUTH_STATE_FILE", "/opt/warehouse-recorder/storage_state.json")
ARCHIVE_DIR = Path(os.environ.get("ARCHIVE_DIR", "/opt/warehouse-recorder/segments"))
DEBUG_DIR = Path(os.environ.get("DEBUG_DIR", "/opt/warehouse-recorder/debug_screenshots"))

# The original Chromebook version used 60s and it was reliable at that
# length. Push this up gradually (try 120, then 300...) and check the logs
# and debug screenshots for new failures before trusting a longer value for
# real 24/7 use. Treat this as untested until you've watched it run clean
# for a while at each step.
RECORD_SECONDS = 120

HEADLESS = True

LOGGED_OUT_TEXT = "Escanea el código QR"
RECORD_BUTTON_SELECTOR = ".videoTool_box__3vg64 > span > .SVG_cs-wrapper__3Cu4D > svg"

MAX_SETUP_ATTEMPTS = 3
MAX_STOP_ATTEMPTS = 3
FEED_CHECK_INTERVAL_S = 5
STEP_TIMEOUT_MS = 15_000
PAGE_LOAD_TIMEOUT_MS = 30_000
# -----------------------------

DEBUG_DIR.mkdir(parents=True, exist_ok=True)
ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)


def log(msg: str):
    print(f"[{datetime.datetime.now():%H:%M:%S}] {msg}", flush=True)


def screenshot(page, tag: str):
    path = DEBUG_DIR / f"{tag}_{datetime.datetime.now():%Y%m%d_%H%M%S}.png"
    try:
        page.screenshot(path=str(path))
        log(f"Saved debug screenshot: {path}")
    except Exception as e:
        log(f"Couldn't save screenshot: {e}")


def get_save_dir() -> Path:
    """Today's folder in the local segments archive (pulled off nightly by pull_footage.sh)."""
    day_folder = ARCHIVE_DIR / datetime.datetime.now().strftime("%Y-%m-%d")
    day_folder.mkdir(parents=True, exist_ok=True)
    return day_folder


def is_logged_out(page) -> bool:
    locator = page.locator(f"text={LOGGED_OUT_TEXT}")
    return locator.count() > 0 and locator.first.is_visible()


def dismiss_error_if_present(page, wait_ms: int = 5000) -> bool:
    """
    The server occasionally shows a 'Failed to connect' error with a Retry
    link -- both while loading the camera feed AND mid-recording if the
    stream drops. Check briefly; don't block if it's not there.
    """
    try:
        retry_btn = page.get_by_text("Retry")
        retry_btn.wait_for(state="visible", timeout=wait_ms)
        log("Error screen detected — clicking Retry.")
        retry_btn.click()
        return True
    except PWTimeout:
        return False


def retry_step(page, description: str, action, attempts: int = 2, delay: float = 2.0):
    last_err = None
    for attempt in range(1, attempts + 1):
        try:
            action(page)
            return
        except (PWTimeout, Exception) as e:
            last_err = e
            log(f"Step '{description}' failed (attempt {attempt}/{attempts}): {e}")
            dismiss_error_if_present(page, wait_ms=3000)
            time.sleep(delay)
    screenshot(page, f"failed_{description.replace(' ', '_')}")
    raise RuntimeError(f"Step '{description}' failed after {attempts} attempts") from last_err


def setup_video_view(page):
    for attempt in range(1, MAX_SETUP_ATTEMPTS + 1):
        try:
            log(f"Setup attempt {attempt}/{MAX_SETUP_ATTEMPTS}: loading page...")
            page.goto(SITE_URL, timeout=PAGE_LOAD_TIMEOUT_MS, wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle", timeout=PAGE_LOAD_TIMEOUT_MS)

            if is_logged_out(page):
                raise RuntimeError(
                    "Session expired — needs a fresh QR login. "
                    "Run login_and_save_state.py on your Mac and re-upload storage_state.json."
                )

            retry_step(page, "click Share tab",
                       lambda p: p.get_by_role("tab", name="Share").click(timeout=STEP_TIMEOUT_MS))

            retry_step(page, "click device group",
                       lambda p: p.get_by_text("Área de Carga").click(timeout=STEP_TIMEOUT_MS))

            retry_step(page, "click device card",
                       lambda p: p.locator(".sharCard_device__FdOAx").first.click(timeout=STEP_TIMEOUT_MS))

            try:
                page.locator("#rc-tabs-1-panel-2").press("ControlOrMeta+-")
            except Exception:
                pass

            retry_step(page, "click fullscreen icon",
                       lambda p: p.locator("use").nth(1).click(timeout=STEP_TIMEOUT_MS))

            dismiss_error_if_present(page, wait_ms=6000)

            page.locator(RECORD_BUTTON_SELECTOR).first.wait_for(
                state="visible", timeout=STEP_TIMEOUT_MS
            )
            log("Video view ready.")
            return

        except Exception as e:
            log(f"Setup attempt {attempt} failed: {e}")
            screenshot(page, f"setup_attempt_{attempt}")
            if attempt < MAX_SETUP_ATTEMPTS:
                log("Reloading and retrying setup...")
                time.sleep(3)
            else:
                raise RuntimeError("Could not reach a working video view after all attempts") from e


def wait_while_monitoring_feed(page, duration_seconds: int):
    """
    Waits out the recording duration, but checks periodically for the
    'Failed to connect' overlay (feed dropped mid-recording) and clicks
    Retry if it shows up, instead of blindly sleeping.
    """
    elapsed = 0
    while elapsed < duration_seconds:
        time.sleep(FEED_CHECK_INTERVAL_S)
        elapsed += FEED_CHECK_INTERVAL_S
        if dismiss_error_if_present(page, wait_ms=1000):
            log("Feed reconnected mid-recording.")


def stop_recording_with_retry(page, record_btn):
    """
    Clicks stop expecting a download. If the feed had dropped, there's
    nothing to stop yet — detect that, click Retry, and try stopping again.
    """
    for attempt in range(1, MAX_STOP_ATTEMPTS + 1):
        try:
            with page.expect_download(timeout=STEP_TIMEOUT_MS) as download_info:
                record_btn.click(timeout=STEP_TIMEOUT_MS)
            return download_info.value
        except PWTimeout:
            log(f"Stop attempt {attempt}/{MAX_STOP_ATTEMPTS}: no download triggered.")
            screenshot(page, f"stop_attempt_{attempt}")
            if dismiss_error_if_present(page, wait_ms=5000):
                log("Feed was down — clicked Retry, will attempt stop again.")
            time.sleep(2)
    raise RuntimeError("Could not trigger a download after multiple stop attempts")


def do_recording_cycle(page):
    record_btn = page.locator(RECORD_BUTTON_SELECTOR).first

    retry_step(page, "start recording",
               lambda p: record_btn.click(timeout=STEP_TIMEOUT_MS))

    log(f"Recording started, waiting {RECORD_SECONDS}s (monitoring for feed drops)...")
    wait_while_monitoring_feed(page, RECORD_SECONDS)

    download = stop_recording_with_retry(page, record_btn)

    save_dir = get_save_dir()
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = save_dir / f"area_de_carga_{timestamp}.mp4"
    download.save_as(dest)
    log(f"Saved clip: {dest}")


def run(loop: bool):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(
            storage_state=AUTH_STATE_FILE,
            accept_downloads=True,
        )
        page = context.new_page()

        try:
            setup_video_view(page)

            if loop:
                log("Looping — recording clips back-to-back. Ctrl+C to stop.")
                while True:
                    try:
                        do_recording_cycle(page)
                    except Exception as e:
                        log(f"Recording cycle failed: {e} — re-running setup and continuing.")
                        setup_video_view(page)
            else:
                do_recording_cycle(page)

        except KeyboardInterrupt:
            log("Stopped by user.")
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    run(loop="--loop" in sys.argv)
