#!/usr/bin/env python3
"""
Copy today's SDA Bocconi (IMB9) event cards into a readable text report.

Each run opens the public IMB9 planner, selects the requested day, copies the
event cards shown in the browser, and writes a clean human-readable report.

Usage:
    python download_bocconi_calendar.py
    python download_bocconi_calendar.py --out "C:/Users/me/Calendars"
    python download_bocconi_calendar.py --date 2026-11-23   # a specific day instead of today

Requires Selenium and a locally installed Chrome browser. The public planner
link can be replaced with a newer share link if the university changes it.
"""

import argparse
import os
import re
import shutil
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

PUBLIC_CALENDAR_URL = (
    "https://unibocconi.prod.up.cineca.it/calendarioPubblico/"
    "linkCalendarioId=69fca9d36e27dd001eb36371"
)
LOCAL_TZ = ZoneInfo("Asia/Calcutta")
LOCAL_TZ_LABEL = "Asia/Calcutta"


def _find_executable(*names: str, extra: tuple[str, ...] = ()) -> str | None:
    """Return the first existing executable among PATH names and fixed paths."""
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    for path in extra:
        if Path(path).is_file() and os.access(path, os.X_OK):
            return path
    return None


def _clean_summary(value: str) -> str:
    value = " ".join(value.split())
    return value.title() if value.isupper() else value


def _capture_event_cards(driver: webdriver.Chrome, day: date) -> list[dict[str, str]]:
    """Extract the date, times, summary, and details from visible event cards."""
    heading = driver.find_element(By.CSS_SELECTOR, "h2").text
    if day.strftime("%d %B %Y") not in heading:
        raise RuntimeError(
            f"Planner is showing {heading!r}, not the requested date {day}."
        )

    events = []
    for card in driver.find_elements(By.CSS_SELECTOR, "a.fc-event[title]"):
        card_lines = [line.strip() for line in
                      (card.get_attribute("title") or card.text).splitlines()
                      if line.strip()]
        if not card_lines:
            continue

        time_match = re.match(r"(\d{2}:\d{2})\s*-\s*(\d{2}:\d{2})",
                              card_lines[0])
        all_day = card_lines[0].lower() in {"all day", "all-day"}
        summary_index = 1 if time_match or all_day else 0
        if summary_index >= len(card_lines):
            continue

        start_time = time_match.group(1) if time_match else "—"
        end_time = time_match.group(2) if time_match else "—"
        display_time = (f"{start_time}–{end_time}" if time_match
                        else "All day" if all_day else "Time not listed")
        summary = _clean_summary(card_lines[summary_index])
        details = card_lines[summary_index + 1:]
        card_text = "\n".join([*card_lines, card.text or ""])
        classe_match = re.search(r"\bClasse\s+([A-Za-z0-9-]+)", card_text, re.IGNORECASE)
        classe = f"Classe {classe_match.group(1)}" if classe_match else "—"
        details = list(dict.fromkeys(details))
        details = [
            detail for detail in details
            if len(detail) < 8 or not any(
                detail.casefold() in other.casefold()
                for other in details if other != detail
            )
        ]

        events.append({
            "date": day.strftime("%A, %d %B %Y"),
            "time": display_time,
            "summary": summary,
            "start_time": start_time,
            "end_time": end_time,
            "classe": classe,
            "description": "; ".join(details) if details else "—",
        })
    return events


def capture_visible_events(day: date, headless: bool = True) -> list[dict[str, str]]:
    """Open the public planner and copy event cards for the requested date."""
    options = webdriver.ChromeOptions()
    options.add_argument("--window-size=1280,900")
    if headless:
        options.add_argument("--headless=new")
    # Cloud servers (e.g. Streamlit Community Cloud) run Linux with Chromium
    # installed from packages.txt and need these extra flags.
    if sys.platform.startswith("linux"):
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
    chrome_bin = _find_executable(
        "chromium", "chromium-browser", "google-chrome", "google-chrome-stable",
        extra=("/usr/bin/chromium", "/usr/lib/chromium/chromium",
               "/usr/bin/chromium-browser", "/snap/bin/chromium"))
    driver_bin = _find_executable(
        "chromedriver",
        extra=("/usr/bin/chromedriver", "/usr/lib/chromium/chromedriver",
               "/usr/lib/chromium-browser/chromedriver"))
    if sys.platform.startswith("linux") and chrome_bin and driver_bin:
        # Use the system Chromium + matching driver (installed via packages.txt)
        # so Selenium doesn't download a driver that can't run on the server.
        options.binary_location = chrome_bin
        driver = webdriver.Chrome(service=Service(driver_bin), options=options)
    elif sys.platform.startswith("linux") and Path("/home/appuser").exists():
        raise RuntimeError(
            f"Found browser={chrome_bin!r}, driver={driver_bin!r}. Both are needed: "
            "make sure packages.txt (with 'chromium' and 'chromium-driver') is in "
            "the repo's top folder, then reboot the app.")
    else:
        driver = webdriver.Chrome(options=options)
    try:
        wait = WebDriverWait(driver, 40)
        driver.get(PUBLIC_CALENDAR_URL)
        wait.until(EC.presence_of_element_located(
            (By.CSS_SELECTOR, "button[aria-label='Export to iCal']")
        ))

        date_inputs = driver.find_elements(By.CSS_SELECTOR, "input.md-datepicker-input")
        if date_inputs:
            date_input = date_inputs[0]
            requested_date = day.strftime("%m/%d/%Y")
            if date_input.get_attribute("value") != requested_date:
                date_input.click()
                date_input.send_keys(Keys.CONTROL, "a")
                date_input.send_keys(requested_date)
                date_input.send_keys(Keys.TAB)
                wait.until(lambda browser: day.strftime("%d %B %Y") in
                           browser.find_element(By.CSS_SELECTOR, "h2").text)

        try:
            wait.until(lambda browser: browser.find_elements(
                By.CSS_SELECTOR, "a.fc-event[title]"
            ))
        except TimeoutException:
            # A date with no sessions is valid; allow the planner to finish loading.
            pass
        return _capture_event_cards(driver, day)
    finally:
        driver.quit()


def format_report(events: list[dict[str, str]], day: date) -> str:
    """Format captured event data as a compact, human-readable report."""
    lines = [
        "IMB9 Calendar",
        f"Date: {day:%A, %d %B %Y}",
        f"Sessions: {len(events)}",
        f"Timezone: {LOCAL_TZ_LABEL}",
        "=" * 64,
    ]
    if not events:
        lines.append("No event cards were visible for this date.")
    for index, event in enumerate(events, start=1):
        lines.extend([
            f"Event {index}",
            f"  Date        : {event['date']}",
            f"  Time        : {event['time']}",
            f"  Summary     : {event['summary']}",
            f"  Start time  : {event['start_time']}",
            f"  End time    : {event['end_time']}",
            f"  Classe      : {event['classe']}",
            f"  Description : {event['description']}",
            "",
        ])
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Copy today's Bocconi IMB9 event cards into a text report."
    )
    parser.add_argument("--out", default=str(Path(__file__).resolve().parent),
                        help="Folder for the report (default: script's folder)")
    parser.add_argument("--date", help="Date to capture as YYYY-MM-DD (default: today)")
    parser.add_argument("--show-browser", action="store_true",
                        help="Show Chrome instead of running it in the background")
    args = parser.parse_args()

    try:
        day = (datetime.strptime(args.date, "%Y-%m-%d").date() if args.date
               else datetime.now(LOCAL_TZ).date())
    except ValueError:
        parser.error("--date must use YYYY-MM-DD format")

    try:
        events = capture_visible_events(day, headless=not args.show_browser)
    except Exception as e:
        print(f"FAILED to capture calendar for {day}: {e}", file=sys.stderr)
        return 1

    out_dir = Path(args.out).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"IMB9_calendar_{day:%Y-%m-%d}.txt"
    target.write_text(format_report(events, day), encoding="utf-8", newline="")

    if not events:
        print(f"No event cards were visible for {day:%A %d %b %Y}. Saved report to {target}")
    else:
        print(f"Saved {len(events)} session(s) for {day:%A %d %b %Y} to {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())