#!/usr/bin/env python3
"""
Run this ON YOUR MAC, not the VPS — whenever the VPS session dies
(expected every 1-3 days). It opens a real, visible browser window so
you can scan the QR code with the Steren Home app, then saves the
resulting login session to a file you upload to the VPS.

Usage:
    python3 login_and_save_state.py
    (scan the QR when prompted, press Enter once the live feed is playing)
    scp storage_state.json youruser@your-vps-ip:/opt/warehouse-recorder/storage_state.json
"""

from pathlib import Path
from playwright.sync_api import sync_playwright

LOGIN_URL = "https://protect-us.ismartlife.me/login"
OUTPUT_FILE = Path("storage_state.json")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.goto(LOGIN_URL)

    print("\n>>> Scan the QR code shown in the browser window with the Steren Home app.")
    input(">>> Press Enter here once you've scanned it and see the live video playing... ")

    context.storage_state(path=str(OUTPUT_FILE))
    print(f"\nSaved session to {OUTPUT_FILE.resolve()}")
    print("Now upload it to the VPS with:")
    print(f"  scp {OUTPUT_FILE} youruser@your-vps-ip:/opt/warehouse-recorder/storage_state.json")

    browser.close()
