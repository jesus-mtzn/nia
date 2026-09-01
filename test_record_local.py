#!/usr/bin/env python3
"""
Run this ON YOUR MAC to get a first test clip TODAY, before the VPS
exists. Uses the same recording logic as record.py, but:
  - runs just once (not a 24/7 loop)
  - records a short 2-minute clip so you can check quality fast
  - assumes you've already run login_and_save_state.py in this same
    folder, so storage_state.json exists right here

Once you're happy with the quality, record.py is the version to deploy
to the VPS for the real 24/7 job.
"""

import time
from datetime import datetime
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

PLAYBACK_URL = "https://protect-us.ismartlife.me/playback"
STORAGE_STATE = Path("storage_state.json")
OUTPUT_DIR = Path("test_clips")
TEST_MINUTES = 2

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
        a.download = 'test_clip.webm';
        document.body.appendChild(a);
        a.click();
        setTimeout(resolve, 200);
    };
    window.__recorder.stop();
})"""

if not STORAGE_STATE.exists():
    raise SystemExit(
        "storage_state.json not found. Run login_and_save_state.py first "
        "(in this same folder) and scan the QR code."
    )

OUTPUT_DIR.mkdir(exist_ok=True)

with sync_playwright() as p:
    # headless=False so you can watch it work the first time
    browser = p.chromium.launch(headless=False)
    context = browser.new_context(
        storage_state=str(STORAGE_STATE),
        accept_downloads=True,
        viewport={"width": 1920, "height": 1080},
    )
    page = context.new_page()
    page.goto(PLAYBACK_URL, wait_until="networkidle")

    if "/login" in page.url:
        raise SystemExit("Got redirected to login — session invalid, re-run login_and_save_state.py")

    print("Waiting for video to start playing...")
    page.wait_for_function(
        "() => { const v = document.querySelector('video'); return v && v.readyState >= 2 && !v.paused; }",
        timeout=20000,
    )

    print(f"Recording for {TEST_MINUTES} minute(s)...")
    page.evaluate(START_RECORDER_JS, 1000)
    time.sleep(TEST_MINUTES * 60)

    with page.expect_download() as dl_info:
        page.evaluate(STOP_AND_DOWNLOAD_JS)
    download = dl_info.value

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    dest = OUTPUT_DIR / f"test_{timestamp}.webm"
    download.save_as(str(dest))

    context.close()
    browser.close()

print(f"\nDone! Test clip saved to: {dest.resolve()}")
print("Open it and check: is the quality as good as the site's own record button?")
