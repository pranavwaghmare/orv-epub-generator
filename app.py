"""Streamlit user interface for ORV Flamecomics Archiver & EPUB Generator."""

from __future__ import annotations

import os
from pathlib import Path
import time
import streamlit as st

from src.archive_validator import validate_archive
from src.job_manager import JobManager
from src.parser import format_chapter_display

st.set_page_config(
    page_title="ORV Archiver & EPUB Generator",
    page_icon="📖",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom CSS for polished typography and readability
st.markdown(
    """
    <style>
    .main-header {
        font-size: 2.2rem;
        font-weight: 700;
        color: #1E3A8A;
        margin-bottom: 0.2rem;
    }
    .sub-header {
        font-size: 1.05rem;
        color: #4B5563;
        margin-bottom: 1.5rem;
    }
    .metric-card {
        background-color: #F3F4F6;
        padding: 1rem;
        border-radius: 8px;
        border-left: 4px solid #3B82F6;
    }
    .status-badge {
        display: inline-block;
        padding: 0.25rem 0.6rem;
        border-radius: 4px;
        font-weight: 600;
        font-size: 0.85rem;
    }
    .log-box {
        font-family: 'Courier New', monospace;
        font-size: 0.82rem;
        line-height: 1.4;
        background-color: #111827;
        color: #10B981;
        padding: 1rem;
        border-radius: 6px;
        height: 280px;
        overflow-y: scroll;
        white-space: pre-wrap;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_resource
def get_job_manager(base_dir: str = "data/jobs") -> JobManager:
    return JobManager(base_jobs_dir=Path(base_dir))


job_manager = get_job_manager()


# Sidebar: App info & stats
with st.sidebar:
    st.markdown("### 📖 ORV Archiver")
    st.markdown(
        """
        **Omniscient Reader's Viewpoint**
        Flamecomics (WeTriedTLS) Archiver & EPUB Generator.
        
        - **Phase 1**: HTML & inline illustration archival.
        - **Phase 2**: Offline EPUB compilation.
        - **Resumable**: Persistent manifests with atomic state checkpoints.
        """
    )
    st.divider()
    jobs = job_manager.list_jobs()
    st.metric("Total Jobs", len(jobs))
    busy = job_manager.is_busy()
    if busy:
        st.info("⚡ Background Worker Active")
    else:
        st.caption("Worker Status: Idle")


# Header
st.markdown('<div class="main-header">Omniscient Reader\'s Viewpoint — Archiver & EPUB Generator</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="sub-header">Archive chapters from Flamecomics and convert saved content into standard EPUBs with inline illustrations.</div>',
    unsafe_allow_html=True,
)

# Tab Navigation
tab_existing, tab_new = st.tabs(["📂 Existing Jobs & Conversion", "🚀 Start New Archival Job"])


# ==========================================
# TAB 1: EXISTING JOBS & EPUB CONVERSION
# ==========================================
with tab_existing:
    jobs = job_manager.list_jobs()
    if not jobs:
        st.info("No jobs found yet. Switch to the **'Start New Archival Job'** tab to begin.")
    else:
        job_options = {f"{m.job_name} ({m.job_id})": m.job_id for m in jobs}
        selected_label = st.selectbox("Select Job to View / Manage:", list(job_options.keys()))
        selected_job_id = job_options[selected_label]
        manifest = job_manager.get_job(selected_job_id)

        if manifest:
            stats = manifest.get_stats()
            
            # Overview Metrics
            col1, col2, col3, col4, col5 = st.columns(5)
            with col1:
                status_color = {
                    "completed": "🟢 Completed",
                    "in_progress": "🔵 In Progress",
                    "paused": "🟠 Paused",
                    "failed": "🔴 Failed",
                    "pending": "⚪ Pending",
                }.get(manifest.status, manifest.status)
                st.metric("Status", status_color)
            with col2:
                st.metric("Chapters", f"{stats['completed_chapters']} / {stats['total_chapters']}")
            with col3:
                st.metric("Failed Chapters", stats["failed_chapters"])
            with col4:
                st.metric("Images", f"{stats['completed_images']} / {stats['total_images']}")
            with col5:
                phase_label = manifest.current_phase.capitalize()
                st.metric("Current Phase", phase_label)

            st.caption(f"**Target Range:** Ch {manifest.detected_starting_chapter or '?'} to Ch {manifest.requested_ending_chapter} | **Source:** `{manifest.starting_url}` | **Last Activity:** {manifest.updated_at[:19]} UTC")

            # Action Toolbar
            st.markdown("#### Actions")
            t_col1, t_col2, t_col3, t_col4 = st.columns(4)
            is_busy = job_manager.is_busy()

            with t_col1:
                if manifest.status in ["paused", "failed", "pending", "in_progress"]:
                    if st.button("▶️ Resume Archival", disabled=is_busy, use_container_width=True):
                        job_manager.start_archival(selected_job_id)
                        st.rerun()
                else:
                    st.button("✅ Archival Complete", disabled=True, use_container_width=True)

            with t_col2:
                has_failures = stats["failed_chapters"] > 0 or stats["failed_images"] > 0
                if st.button("🔄 Retry Failed", disabled=is_busy or not has_failures, use_container_width=True):
                    job_manager.retry_failed(selected_job_id)
                    st.rerun()

            with t_col3:
                if st.button("⏸️ Pause", disabled=not is_busy, use_container_width=True):
                    job_manager.request_pause()
                    st.warning("Pause requested. The active step will conclude safely.")

            with t_col4:
                if st.button("🔍 Validate Archive", use_container_width=True):
                    st.session_state[f"show_validation_{selected_job_id}"] = True

            # EPUB Conversion Section
            st.divider()
            st.markdown("#### Phase 2: EPUB Conversion")
            e_col1, e_col2 = st.columns([2, 1])

            with e_col1:
                override = st.checkbox(
                    "⚠️ Generate Anyway (Override validation warnings / gaps)",
                    value=False,
                    help="Enable to build EPUB even if some chapters or assets were reported missing.",
                )
                if st.button("📚 Convert to EPUB", disabled=is_busy or stats["completed_chapters"] == 0, type="primary"):
                    ok = job_manager.start_conversion(selected_job_id, force_override=override)
                    if ok:
                        st.success("EPUB conversion initiated!")
                        st.rerun()
                    else:
                        st.error("Worker is currently busy with another operation.")

            with e_col2:
                if stats["epub_ready"] and manifest.conversion.get("epub_path"):
                    epub_path = manifest.job_dir / manifest.conversion["epub_path"]
                    if epub_path.exists():
                        with open(epub_path, "rb") as f:
                            epub_bytes = f.read()
                        st.download_button(
                            label=f"📥 Download {epub_path.name}",
                            data=epub_bytes,
                            file_name=epub_path.name,
                            mime="application/epub+zip",
                            use_container_width=True,
                        )
                        st.caption(f"Size: {len(epub_bytes) / 1024 / 1024:.2f} MB | Saved at `{epub_path}`")

            # Validation Report Display (if requested)
            if st.session_state.get(f"show_validation_{selected_job_id}", False):
                st.markdown("##### 🔍 Archive Validation Report")
                res = validate_archive(manifest)
                if res.is_valid:
                    st.success("✅ Archive is complete, consistent, and ready for conversion!")
                else:
                    st.warning("⚠️ Validation identified issues with the archive:")
                    for line in res.detailed_report:
                        st.write(line)

            # Chapters Table
            with st.expander("📖 View Discovered Chapters Table", expanded=False):
                if manifest.chapters:
                    ch_data = []
                    for c in manifest.chapters:
                        ch_data.append({
                            "Chapter": format_chapter_display(c.chapter_number),
                            "Title": c.title,
                            "Status": c.status,
                            "Images": f"{sum(1 for i in c.images if i.status == 'completed')}/{len(c.images)}",
                            "Error": c.error or "",
                        })
                    st.dataframe(ch_data, use_container_width=True)
                else:
                    st.write("No chapters discovered yet.")


# ==========================================
# TAB 2: START NEW ARCHIVAL JOB
# ==========================================
with tab_new:
    st.markdown("#### Configure New Archival Job")
    
    with st.form("new_job_form"):
        starting_url = st.text_input(
            "Starting Chapter URL *",
            value="https://flamecomics.xyz/novel/8/580e7d866052dfd8",
            help="Example: https://flamecomics.xyz/novel/8/580e7d866052dfd8",
        )
        col_end, col_name = st.columns([1, 2])
        with col_end:
            ending_chapter = st.number_input(
                "Ending Chapter Number *",
                min_value=1.0,
                max_value=2000.0,
                value=5.0,
                step=1.0,
                format="%.1f",
            )
        with col_name:
            custom_job_name = st.text_input(
                "Optional Job Name",
                placeholder="e.g. ORV Prologue & Episode 1",
            )
        
        col_out, _ = st.columns([2, 1])
        with col_out:
            output_dir = st.text_input(
                "Jobs Output Directory",
                value="data/jobs",
                help="Base directory where job folders and manifests are isolated.",
            )

        f_col1, f_col2 = st.columns([1, 1])
        with f_col1:
            inspect_pressed = st.form_submit_button("🔍 Inspect Starting Chapter")
        with f_col2:
            start_pressed = st.form_submit_button("🚀 Start Archival Job", type="primary")

    # Handle Inspect
    if inspect_pressed:
        if not starting_url.strip():
            st.error("Please provide a starting chapter URL.")
        else:
            with st.spinner("Connecting to Flamecomics and inspecting starting chapter..."):
                ch_num, ch_title, err = job_manager.inspect_starting_url(starting_url.strip())
                if err:
                    st.error(f"Failed to inspect page: {err}")
                else:
                    st.success(f"Inspection successful!\n- **Detected Chapter**: Chapter {format_chapter_display(ch_num or 0.0)}\n- **Title**: {ch_title}")
                    st.session_state["inspected_start_ch"] = ch_num

    # Handle Start Job
    if start_pressed:
        if not starting_url.strip():
            st.error("Starting chapter URL is required.")
        else:
            with st.spinner("Validating and creating job..."):
                # Probe starting URL
                ch_num, ch_title, err = job_manager.inspect_starting_url(starting_url.strip())
                if err:
                    st.error(f"Cannot start job: starting chapter URL inspection failed ({err}).")
                elif ch_num is not None and ending_chapter < ch_num:
                    st.error(f"Invalid range: Requested ending chapter ({format_chapter_display(ending_chapter)}) is earlier than detected starting chapter ({format_chapter_display(ch_num)}).")
                else:
                    new_manifest = job_manager.create_job(
                        starting_url=starting_url.strip(),
                        ending_chapter=ending_chapter,
                        detected_starting_chapter=ch_num,
                        job_name=custom_job_name.strip() if custom_job_name else None,
                    )
                    st.success(f"Created Job `{new_manifest.job_name}`! Starting background archival...")
                    job_manager.start_archival(new_manifest.job_id)
                    time.sleep(0.5)
                    st.rerun()


# ==========================================
# SECTION C: LIVE PROGRESS AND RECENT LOGS
# ==========================================
st.divider()
st.markdown("### 📊 Live Progress & Activity")


@st.fragment(run_every="1s")
def render_live_progress():
    state = job_manager.get_state()
    active = state.is_running
    
    col_stat, col_bar = st.columns([1, 3])
    with col_stat:
        if active:
            st.markdown(f"**Status:** 🏃 **Running** ({state.current_phase.capitalize()})")
        else:
            st.markdown("**Status:** 💤 **Idle**")
        st.markdown(f"**Operation:** `{state.current_op}`")

    with col_bar:
        st.progress(state.progress_ratio, text=f"{int(state.progress_ratio * 100)}% Complete")

    if state.errors:
        with st.expander("⚠️ Recent Errors Encountered", expanded=True):
            for err in state.errors[-5:]:
                st.error(err)

    # Activity Log Box
    log_text = "\n".join(state.logs[-40:]) if state.logs else "No worker logs recorded yet."
    st.markdown(f'<div class="log-box">{log_text}</div>', unsafe_allow_html=True)


render_live_progress()
