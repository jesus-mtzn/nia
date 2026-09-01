#!/usr/bin/env python3
"""
Area de Carga 24/7 recorder — v2, using captureStream() + MediaRecorder.

Why this approach: this camera's HEVC stream is decoded client-side via a
custom WASM decoder feeding a MediaSource buffer (confirmed by inspecting
the site's JS bundle) — not a plain WebRTC video track. That's exactly why
tools like tuya-ipc-terminal, which try to repackage the raw data-channel
stream into RTSP, produce corrupted NAL headers. captureStream() sidesteps
all of that by grabbing the frames *after* the browser has already done
the decoding — same mechanism the site's own "record" button likely uses.

Records in fixed-length segments. Each segment: start a MediaRecorder on
the video element's captured stream, wait, stop it, trigger a browser
download of the resulting Blob, and save that download to disk.
"""

import time
import logging
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# ============================================================
# CONFIG
# ============================================================
LOGIN_URL = "https://protect-us.ismartlife.me/login"
PLAYBACK_URL = "https://protect-us.ismartlife.me/playback"

STORAGE_STATE = Path("/opt/warehouse-recorder/storage_state.json")
ARCHIVE_DIR = Path("/opt/warehouse-recorder/segments")

SEGMENT_MINUTES = 30      # length of each recorded clip
CHUNK_MS = 1000           # how often MediaRecorder hands us a data chunk

# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("/opt/warehouse-recorder/recorder.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("recorder")

START_RECORDER_JS = """(chunkMs) => {
    const video = document.querySelector('video');
    if (!video) throw new Error('no video element found');
    const stream = video.captureStream ? video.captureStream() : video.mozCaptureStream();
    window.__chunks = [];
    const candidates = ['video/webm;codecs=vp9,opus', 'video/webm;codecs=vp8,opus', 'video/webm'];
    const mimeType = candidates.find(m => window.MediaRecorder.isTypeSupported(m)) || '';
    window.__recorder = mimeType ? new MediaRecorder(stream, {mimeType}) : new MediaRecorder(stream);
    window.__recorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) window.__chunks.push(e.data);
    };
    window.__recorder.start(chunkMs);
}"""

STOP_AND_DOWNLOAD_JS = """() => new Promise((resolve) => {
    window.__recorder.onstop = () => {
        const blob = new Blob(window.__chunks, {type: window.__recorder.mimeType || 'video/webm'});
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'segment.webm';
        document.body.appendChild(a);
        a.click();
        setTimeout(resolve, 200);
    };
    window.__recorder.stop();
})"""


def is_logged_out(page) -> bool:
    """If we got bounced back to /login, the session's dead."""
    return "/login" in page.url


def record_one_segment() -> bool:
    """
    Records a single SEGMENT_MINUTES-long clip.
    Returns True on success (or a transient hiccup worth retrying),
    False if the session is logged out — caller should pause.
    """
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            storage_state=str(STORAGE_STATE) if STORAGE_STATE.exists() else None,
            accept_downloads=True,
            viewport={"width": 1920, "height": 1080},
        )
        page = context.new_page()

        try:
            page.goto(PLAYBACK_URL, wait_until="networkidle", timeout=30000)
        except PWTimeout:
            log.warning("Page load timed out, will retry next loop")
            context.close()
            browser.close()
            return True

        if is_logged_out(page):
            log.error("Session logged out — refresh storage_state.json and re-upload.")
            context.close()
            browser.close()
            return False

        try:
            page.wait_for_function(
                "() => { const v = document.querySelector('video'); "
                "return v && v.readyState >= 2 && !v.paused; }",
                timeout=20000,
            )
        except PWTimeout:
            log.warning("Video never started playing, skipping this segment")
            context.close()
            browser.close()
            return True

        log.info("Recording started, will run for %s minutes", SEGMENT_MINUTES)
        page.evaluate(START_RECORDER_JS, CHUNK_MS)
        time.sleep(SEGMENT_MINUTES * 60)

        with page.expect_download() as dl_info:
            page.evaluate(STOP_AND_DOWNLOAD_JS)
        download = dl_info.value

        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        dest = ARCHIVE_DIR / f"area_de_carga_{timestamp}.webm"
        download.save_as(str(dest))
        log.info("Segment saved: %s", dest)

        context.close()
        browser.close()

    return True


def main():
    log.info("Warehouse recorder starting")
    while True:
        ok = record_one_segment()
        if not ok:
            log.error("Stopping: needs manual re-login. Re-checking in 15 min.")
            time.sleep(15 * 60)
        time.sleep(2)


if __name__ == "__main__":
    main()
