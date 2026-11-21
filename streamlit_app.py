"""Web UI for the IMB9 calendar script. Deploy on Streamlit Community Cloud."""

import calendar
import tempfile
from datetime import date, datetime
from pathlib import Path

import streamlit as st

from download_bocconi_calendar import (
    LOCAL_TZ,
    _format_clock,
    _format_duration,
    batch_availability,
    capture_month_events,
    capture_visible_events,
    format_month_report,
    format_report,
    write_availability_pdf,
)

st.set_page_config(page_title="IMB9 Calendar", page_icon="📅", layout="wide")
st.title("📅 IMB9 Calendar")

today = datetime.now(LOCAL_TZ).date()
mode = st.radio("What do you want?", ["One day", "Whole month"], horizontal=True)

if mode == "One day":
    day = st.date_input("Date", value=today, format="DD/MM/YYYY")
else:
    c1, c2 = st.columns(2)
    month = c1.selectbox("Month", range(1, 13), index=today.month - 1,
                         format_func=lambda m: calendar.month_name[m])
    year = c2.number_input("Year", min_value=2024, max_value=2030,
                           value=today.year, step=1)


def show_day(day: date, events: list[dict[str, str]]) -> None:
    """Render one day's sessions and batch availability."""
    if not events:
        st.info("No sessions.")
        return
    for ev in events:
        st.markdown(f"**{ev['time']}** · {ev['summary']}  \n"
                    f"<small>{ev['classe']} — {ev['description']}</small>",
                    unsafe_allow_html=True)
    availability = batch_availability(events)
    if availability:
        st.markdown("**Batch availability (9 AM – 8 PM)**")
        rows = []
        for classe, intervals in availability.items():
            free = [f"{_format_clock(int(i['start']))}–{_format_clock(int(i['end']))} "
                    f"({_format_duration(int(i['end']) - int(i['start']))})"
                    for i in intervals if i["kind"] == "available"]
            rows.append({"Batch": classe, "Available": ", ".join(free) or "—"})
        st.table(rows)


if st.button("Get calendar", type="primary"):
    try:
        if mode == "One day":
            with st.spinner("Opening the planner (≈20–40 s)..."):
                events = capture_visible_events(day, headless=True)
            events_by_day = {day: events}
            report = format_report(events, day)
            period = day.strftime("%A %d %b %Y")
            stem = f"IMB9_calendar_{day:%Y-%m-%d}"
        else:
            year = int(year)
            with st.spinner(f"Reading every day of {calendar.month_name[month]} {year} "
                            "(can take a few minutes)..."):
                events_by_day = capture_month_events(year, month, headless=True)
            report = format_month_report(events_by_day, year, month)
            period = f"{calendar.month_name[month]} {year}"
            stem = f"IMB9_calendar_{year}-{month:02d}"
    except Exception as e:
        st.error(f"Failed to capture calendar: {e}")
        st.stop()

    with tempfile.TemporaryDirectory() as tmp:
        pdf_path = Path(tmp) / f"{stem}.pdf"
        try:
            write_availability_pdf(events_by_day, pdf_path, period)
            pdf_bytes = pdf_path.read_bytes()
        except Exception as e:
            pdf_bytes = None
            st.warning(f"PDF could not be generated: {e}")

    # Keep results after download-button clicks (which rerun the page).
    st.session_state["result"] = (events_by_day, report, pdf_bytes, period, stem)

if "result" in st.session_state:
    events_by_day, report, pdf_bytes, period, stem = st.session_state["result"]
    total = sum(len(v) for v in events_by_day.values())
    st.success(f"{total} session(s) — {period}")

    d1, d2 = st.columns(2)
    if pdf_bytes:
        d1.download_button("⬇️ Availability PDF", pdf_bytes, file_name=f"{stem}.pdf",
                           mime="application/pdf")
    d2.download_button("⬇️ Text report", report, file_name=f"{stem}.txt")

    if len(events_by_day) == 1:
        (only_day, only_events), = events_by_day.items()
        show_day(only_day, only_events)
    else:
        for d, evs in sorted(events_by_day.items()):
            with st.expander(f"{d:%a %d %b} — {len(evs)} session(s)", expanded=False):
                show_day(d, evs)

    with st.expander("Full plain-text report"):
        st.code(report, language=None)
