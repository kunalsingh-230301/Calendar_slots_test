#!/usr/bin/env python3
"""
Copy today's SDA Bocconi (IMB9) event cards into a readable text report.

Each run opens the public IMB9 planner, selects the requested day, copies the
event cards shown in the browser, and writes a clean human-readable report.

Usage:
    python download_bocconi_calendar.py                    # current month
    python download_bocconi_calendar.py --out "C:/Users/me/Calendars"
    python download_bocconi_calendar.py --month 2026-12    # a specific month
    python download_bocconi_calendar.py --date 2026-11-23  # one specific day

Requires Selenium and a locally installed Chrome browser. The public planner
link can be replaced with a newer share link if the university changes it.
"""

import argparse
import calendar
import os
import re
import shutil
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from selenium import webdriver
from selenium.common.exceptions import StaleElementReferenceException, TimeoutException
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
AVAILABILITY_START = 9 * 60
AVAILABILITY_END = 20 * 60


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
    expected_heading_date = f"{day.day} {day:%B %Y}"
    if expected_heading_date not in heading:
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


def _create_driver(headless: bool) -> webdriver.Chrome:
    options = webdriver.ChromeOptions()
    options.add_argument("--window-size=1280,900")
    if headless:
        options.add_argument("--headless=new")
    if not sys.platform.startswith("linux"):
        return webdriver.Chrome(options=options)

    # Linux / cloud servers (e.g. Streamlit Community Cloud): use the system
    # Chromium + matching driver installed via packages.txt, so Selenium doesn't
    # download its own copy, which can't run there.
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
    if chrome_bin and driver_bin:
        options.binary_location = chrome_bin
        return webdriver.Chrome(service=Service(driver_bin), options=options)
    if Path("/home/appuser").exists():  # running on Streamlit Community Cloud
        raise RuntimeError(
            f"Found browser={chrome_bin!r}, driver={driver_bin!r}. Both are needed: "
            "make sure packages.txt (with 'chromium' and 'chromium-driver') is in "
            "the repo's top folder, then reboot the app.")
    return webdriver.Chrome(options=options)


def _open_planner(driver: webdriver.Chrome) -> WebDriverWait:
    wait = WebDriverWait(driver, 40)
    # FullCalendar renders event times in the browser's timezone. Streamlit
    # hosts commonly run in UTC, so make the browser match the report timezone.
    driver.execute_cdp_cmd(
        "Emulation.setTimezoneOverride", {"timezoneId": LOCAL_TZ_LABEL}
    )
    driver.get(PUBLIC_CALENDAR_URL)
    wait.until(EC.presence_of_element_located(
        (By.CSS_SELECTOR, "button[aria-label='Export to iCal']")
    ))
    return wait


def _select_planner_day(driver: webdriver.Chrome, day: date,
                        wait: WebDriverWait) -> list[dict[str, str]]:
    """Select a day in the open planner and collect its displayed event cards."""
    requested_date = day.strftime("%m/%d/%Y")
    expected_heading_date = f"{day.day} {day:%B %Y}"
    for attempt in range(3):
        try:
            date_inputs = driver.find_elements(
                By.CSS_SELECTOR, "input.md-datepicker-input"
            )
            if not date_inputs:
                raise RuntimeError("The planner date picker was not found.")
            date_input = date_inputs[0]
            if date_input.get_attribute("value") != requested_date:
                date_input.click()
                date_input.send_keys(Keys.CONTROL, "a")
                date_input.send_keys(requested_date)
                date_input.send_keys(Keys.TAB)
            wait.until(lambda browser: expected_heading_date in
                       browser.find_element(By.CSS_SELECTOR, "h2").text)

            try:
                # Empty days take a short timeout; populated dates return when cards appear.
                WebDriverWait(driver, 2).until(lambda browser: browser.find_elements(
                    By.CSS_SELECTOR, "a.fc-event[title]"
                ))
            except TimeoutException:
                pass
            return _capture_event_cards(driver, day)
        except StaleElementReferenceException:
            if attempt == 2:
                raise
    raise RuntimeError(f"Could not select planner date {day} after retries.")


def capture_visible_events(day: date, headless: bool = True) -> list[dict[str, str]]:
    """Open the planner and copy event cards for one date."""
    driver = _create_driver(headless)
    try:
        wait = _open_planner(driver)
        return _select_planner_day(driver, day, wait)
    finally:
        driver.quit()


def capture_month_events(year: int, month: int,
                         headless: bool = True) -> dict[date, list[dict[str, str]]]:
    """Capture every date in a month while reusing one browser session."""
    driver = _create_driver(headless)
    events_by_day: dict[date, list[dict[str, str]]] = {}
    try:
        wait = _open_planner(driver)
        number_of_days = calendar.monthrange(year, month)[1]
        for day_number in range(1, number_of_days + 1):
            day = date(year, month, day_number)
            events_by_day[day] = _select_planner_day(driver, day, wait)
    finally:
        driver.quit()
    return events_by_day


def _format_clock(minutes: int) -> str:
    hour, minute = divmod(minutes % (24 * 60), 60)
    suffix = "AM" if hour < 12 else "PM"
    display_hour = hour % 12 or 12
    return f"{display_hour}:{minute:02d} {suffix}"


def _format_duration(minutes: int) -> str:
    hours, remaining_minutes = divmod(minutes, 60)
    parts = []
    if hours:
        parts.append(f"{hours} hr" if hours == 1 else f"{hours} hrs")
    if remaining_minutes:
        parts.append(f"{remaining_minutes} min")
    return " ".join(parts) or "0 min"


def _classe_order(classe: str) -> tuple[int, str]:
    match = re.search(r"\d+", classe)
    return (int(match.group()) if match else 0, classe)


def batch_availability(
    events: list[dict[str, str]], classes: set[str] | None = None
) -> dict[str, list[dict[str, object]]]:
    """Return available and blocked intervals per batch within the daily window."""
    class_events: dict[str, list[dict[str, object]]] = {}
    for classe in classes or set():
        if classe != "—":
            class_events[classe] = []
    for event in events:
        classe = event["classe"]
        start_match = re.fullmatch(r"(\d{2}):(\d{2})", event["start_time"])
        end_match = re.fullmatch(r"(\d{2}):(\d{2})", event["end_time"])
        if classe == "—" or not start_match or not end_match:
            continue
        start = int(start_match.group(1)) * 60 + int(start_match.group(2))
        end = int(end_match.group(1)) * 60 + int(end_match.group(2))
        if end <= start:
            end += 24 * 60
        class_events.setdefault(classe, []).append({
            "start": start,
            "end": end,
            "summary": event["summary"],
        })

    availability: dict[str, list[dict[str, object]]] = {}
    for classe in sorted(class_events, key=_classe_order):
        sessions = sorted(class_events[classe], key=lambda item: (item["start"], item["end"]))
        blocks: list[tuple[int, int, list[str]]] = []
        for session in sessions:
            start = max(AVAILABILITY_START, int(session["start"]))
            end = min(AVAILABILITY_END, int(session["end"]))
            if start >= end:
                continue
            if blocks and start <= blocks[-1][1]:
                previous_start, previous_end, summaries = blocks[-1]
                summary = str(session["summary"])
                if summary not in summaries:
                    summaries.append(summary)
                blocks[-1] = (previous_start, max(previous_end, end), summaries)
            else:
                blocks.append((start, end, [str(session["summary"])]))

        intervals: list[dict[str, object]] = []
        cursor = AVAILABILITY_START
        for block_start, block_end, summaries in blocks:
            if block_start > cursor:
                intervals.append({
                    "kind": "available", "start": cursor, "end": block_start,
                })
            intervals.append({
                "kind": "blocked", "start": block_start, "end": block_end,
                "summaries": summaries,
            })
            cursor = max(cursor, block_end)
        if cursor < AVAILABILITY_END:
            intervals.append({
                "kind": "available", "start": cursor, "end": AVAILABILITY_END,
            })
        if not intervals:
            intervals.append({
                "kind": "available", "start": AVAILABILITY_START,
                "end": AVAILABILITY_END,
            })
        availability[classe] = intervals
    return availability


def format_report(events: list[dict[str, str]], day: date) -> str:
    """Format event details and between-session availability by Classe."""
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

    lines.extend([
        "Batch availability from 9:00 AM to 8:00 PM",
        "=" * 64,
    ])
    availability_by_class = batch_availability(events)
    if not availability_by_class:
        lines.append("No timed events with a Classe value were available to compare.")
    for classe, intervals in availability_by_class.items():
        lines.append(classe)
        for interval in intervals:
            start = int(interval["start"])
            end = int(interval["end"])
            kind = str(interval["kind"]).title()
            detail = (f" — {' + '.join(interval['summaries'])}"
                      if interval["kind"] == "blocked" else "")
            lines.append(
                f"  {kind:9}: {_format_clock(start)}–{_format_clock(end)} "
                f"({_format_duration(end - start)}){detail}"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def format_month_report(events_by_day: dict[date, list[dict[str, str]]],
                        year: int, month: int) -> str:
    """Format the month as date sections, each with its own batch availability."""
    total_sessions = sum(len(events) for events in events_by_day.values())
    lines = [
        "IMB9 Monthly Calendar",
        f"Month: {calendar.month_name[month]} {year}",
        f"Total sessions: {total_sessions}",
        f"Timezone: {LOCAL_TZ_LABEL}",
        "=" * 64,
    ]
    for day, events in events_by_day.items():
        day_report = format_report(events, day).splitlines()
        lines.extend(["", *day_report[1:]])
    return "\n".join(lines).rstrip() + "\n"


def write_availability_pdf(
    events_by_day: dict[date, list[dict[str, str]]], target: Path,
    period_title: str,
) -> None:
    """Create a visual PDF containing only daily batch availability and blocked periods."""
    all_classes = {
        event["classe"]
        for day_events in events_by_day.values()
        for event in day_events
        if event["classe"] != "—"
    }
    page_width, _ = landscape(A4)
    doc = SimpleDocTemplate(
        str(target), pagesize=landscape(A4),
        rightMargin=15 * mm, leftMargin=15 * mm,
        topMargin=19 * mm, bottomMargin=15 * mm,
        title=f"IMB9 Batch Availability — {period_title}",
        author="IMB9 Calendar Script",
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "AvailabilityTitle", parent=styles["Title"], fontName="Helvetica-Bold",
        fontSize=20, leading=24, textColor=colors.HexColor("#17324D"),
        alignment=TA_LEFT, spaceAfter=3 * mm,
    )
    subtitle_style = ParagraphStyle(
        "AvailabilitySubtitle", parent=styles["Normal"], fontSize=9,
        textColor=colors.HexColor("#536579"), spaceAfter=5 * mm,
    )
    day_style = ParagraphStyle(
        "AvailabilityDay", parent=styles["Heading2"], fontName="Helvetica-Bold",
        fontSize=15, leading=18, textColor=colors.HexColor("#17324D"),
        spaceAfter=3 * mm,
    )
    cell_style = ParagraphStyle(
        "AvailabilityCell", parent=styles["BodyText"], fontSize=8.5,
        leading=12, spaceAfter=1.5 * mm,
    )
    small_style = ParagraphStyle(
        "AvailabilitySmall", parent=cell_style, fontSize=7.5, leading=10,
        textColor=colors.HexColor("#536579"),
    )
    header_style = ParagraphStyle(
        "AvailabilityHeader", parent=small_style, fontName="Helvetica-Bold",
        textColor=colors.white,
    )

    def page_footer(canvas, _doc) -> None:
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#DCE4EC"))
        canvas.line(15 * mm, 11 * mm, page_width - 15 * mm, 11 * mm)
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#66788A"))
        canvas.drawString(15 * mm, 7 * mm, f"{period_title} · {LOCAL_TZ_LABEL}")
        canvas.drawRightString(page_width - 15 * mm, 7 * mm, f"Page {_doc.page}")
        canvas.restoreState()

    story = []
    ordered_days = sorted(events_by_day)
    for day_index, day in enumerate(ordered_days):
        if day_index:
            story.append(PageBreak())
        day_events = events_by_day[day]
        availability = batch_availability(day_events, all_classes)
        story.extend([
            Paragraph("IMB9 · Batch availability", title_style),
            Paragraph(f"{day:%A, %d %B %Y}", day_style),
            Paragraph(
                f"9:00 AM–8:00 PM"
                f" &nbsp; | &nbsp; {len(day_events)} scheduled session(s)",
                subtitle_style,
            ),
        ])

        if not availability:
            story.append(Paragraph(
                "No batch/Classe information was available for this date.", cell_style
            ))
            continue

        table_data = [[
            Paragraph("BATCH", header_style),
            Paragraph("AVAILABLE", header_style),
            Paragraph("BLOCKED · SCHEDULED SESSIONS", header_style),
        ]]
        for classe, intervals in availability.items():
            available_lines = []
            blocked_lines = []
            for interval in intervals:
                start = int(interval["start"])
                end = int(interval["end"])
                span = f"{_format_clock(start)}–{_format_clock(end)}"
                duration = _format_duration(end - start)
                if interval["kind"] == "available":
                    available_lines.append(f"{span} <font color='#536579'>({duration})</font>")
                else:
                    summaries = ", ".join(str(summary) for summary in interval["summaries"])
                    blocked_lines.append(
                        f"{span} <font color='#536579'>({duration})</font>"
                        f" — {escape(summaries)}"
                    )
            table_data.append([
                Paragraph(f"<b>{escape(classe)}</b>", cell_style),
                Paragraph("<br/>".join(available_lines) or "—", cell_style),
                Paragraph("<br/>".join(blocked_lines) or "No scheduled sessions", cell_style),
            ])

        table = Table(
            table_data,
            colWidths=[35 * mm, 79 * mm, page_width - 30 * mm - 114 * mm],
            repeatRows=1,
            hAlign="LEFT",
        )
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#17324D")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BACKGROUND", (0, 1), (-1, -1), colors.white),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1),
             [colors.white, colors.HexColor("#F3F7FA")]),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#DCE4EC")),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ]))
        story.append(table)
        story.append(Spacer(1, 4 * mm))
        story.append(Paragraph(
            "Available = free of scheduled sessions. Overlapping sessions are merged "
            "into one continuous blocked interval.", small_style
        ))

    doc.build(story, onFirstPage=page_footer, onLaterPages=page_footer)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Copy Bocconi IMB9 event cards for a month or a specific date."
    )
    parser.add_argument("--out", default=str(Path(__file__).resolve().parent),
                        help="Folder for the report (default: script's folder)")
    parser.add_argument("--month", help="Month to capture as YYYY-MM (default: current month)")
    parser.add_argument("--date", help="Capture one date as YYYY-MM-DD instead of a whole month")
    parser.add_argument("--show-browser", action="store_true",
                        help="Show Chrome instead of running it in the background")
    args = parser.parse_args()

    if args.month and args.date:
        parser.error("--month and --date cannot be used together")

    try:
        if args.date:
            day = datetime.strptime(args.date, "%Y-%m-%d").date()
            events = capture_visible_events(day, headless=not args.show_browser)
            report = format_report(events, day)
            report_name = f"IMB9_calendar_{day:%Y-%m-%d}.txt"
            availability_by_day = {day: events}
            session_count = len(events)
            period_description = day.strftime("%A %d %b %Y")
        else:
            selected_month = (datetime.strptime(args.month, "%Y-%m")
                              if args.month else datetime.now(LOCAL_TZ))
            year, month = selected_month.year, selected_month.month
            events_by_day = capture_month_events(
                year, month, headless=not args.show_browser
            )
            report = format_month_report(events_by_day, year, month)
            report_name = f"IMB9_calendar_{year}-{month:02d}.txt"
            availability_by_day = events_by_day
            session_count = sum(len(events) for events in events_by_day.values())
            period_description = f"{calendar.month_name[month]} {year}"
    except ValueError:
        parser.error("--date must use YYYY-MM-DD or --month must use YYYY-MM format")
    except Exception as e:
        print(f"FAILED to capture calendar: {e}", file=sys.stderr)
        return 1

    out_dir = Path(args.out).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / report_name
    target.write_text(report, encoding="utf-8", newline="")
    pdf_target = target.with_suffix(".pdf")
    try:
        write_availability_pdf(availability_by_day, pdf_target, period_description)
    except Exception as e:
        print(f"Saved text report, but failed to generate PDF: {e}", file=sys.stderr)
        return 1

    print(f"Saved {session_count} session(s) for {period_description} to {target}")
    print(f"Saved batch-availability PDF to {pdf_target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())