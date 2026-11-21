"""Web UI for the IMB9 calendar script. Deploy on Streamlit Community Cloud."""

from datetime import datetime

import streamlit as st

from download_bocconi_calendar import LOCAL_TZ, capture_visible_events, format_report

st.set_page_config(page_title="IMB9 Calendar", page_icon="📅")
st.title("📅 IMB9 Calendar")

day = st.date_input("Date", value=datetime.now(LOCAL_TZ).date(), format="DD/MM/YYYY")

if st.button("Get calendar", type="primary"):
    with st.spinner("Opening the planner and reading sessions (takes ~20–40 s)..."):
        try:
            events = capture_visible_events(day, headless=True)
        except Exception as e:
            st.error(f"Failed to capture calendar for {day}: {e}")
            st.stop()

    report = format_report(events, day)

    if events:
        st.success(f"{len(events)} session(s) on {day:%A, %d %B %Y}")
        for ev in events:
            with st.container(border=True):
                st.markdown(f"**{ev['time']}** · {ev['summary']}")
                st.caption(f"{ev['classe']} — {ev['description']}")
    else:
        st.info(f"No sessions found for {day:%A, %d %B %Y}.")

    with st.expander("Plain-text report"):
        st.code(report, language=None)

    st.download_button("Download .txt", report,
                       file_name=f"IMB9_calendar_{day:%Y-%m-%d}.txt")
