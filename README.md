# Omniscient Reader's Viewpoint (ORV) — HTML Archiver & EPUB Generator

A reliable, modular Python application built with Streamlit that archives chapters of *Omniscient Reader's Viewpoint* from Flamecomics (hosting the WeTriedTLS translation with editorial quality-control passes) and compiles the archived content into standard-compliant EPUB books with inline illustrations for personal reading.

The application operates in two distinct, independently resumable phases:
1. **Phase 1 — HTML Archival**: Retrieves chapter HTML, extracts clean text (filtering site navigation, reaction boxes, and ads), downloads illustrations, rewrites inline asset references, and saves an isolated local archive with a persistent atomic JSON manifest.
2. **Phase 2 — EPUB Conversion**: Operates completely offline on the saved local archive, validates archive integrity, stages intermediate XHTML, and packages a valid EPUB with navigation table of contents, inline illustrations, and clean typography.

---

## Architecture & Project Structure

```
orv-flamecomics/
├── app.py                     # Streamlit web application & live progress dashboard
├── requirements.txt           # Core Python dependencies
├── pytest.ini                 # Pytest configuration
├── README.md                  # Documentation and Windows setup guide
├── .gitignore                 # Version control exclusions
├── src/
│   ├── __init__.py
│   ├── parser.py              # HTML parsing, DOM extraction, title/num extraction, next link discovery
│   ├── downloader.py          # Robust HTTP fetching, rate-limiting, retries, image decoding/validation
│   ├── manifest.py            # Thread-safe atomic JSON manifest (manifest.json) & disk reconciliation
│   ├── archive_validator.py   # Offline validation of local archive (chapters, files, images, gaps)
│   ├── scraper.py             # Archival pipeline (dynamic discovery, loop detection, asset downloads)
│   ├── epub_converter.py      # Resumable EPUB generation via EbookLib & intermediate staging
│   └── job_manager.py         # Non-blocking background worker management & state synchronization
├── tests/
│   ├── conftest.py            # Fixtures & mock HTML generators
│   ├── test_parser.py         # Tests for title/number extraction, links, element pruning
│   ├── test_manifest.py       # Tests for atomic writes, schema persistence, and crash recovery
│   ├── test_downloader.py     # Tests for retry handling, status codes, and image validation
│   ├── test_scraper.py        # Tests for discovery, pause/resume, loop detection, and retries
│   ├── test_archive_validator.py # Tests for offline archive validation & integrity checks
│   ├── test_epub_converter.py # Tests for EPUB packaging, table of contents, and inline images
│   └── test_job_manager.py    # Tests for thread safety and background worker coordination
└── data/
    └── jobs/
        └── <job_id>/
            ├── manifest.json  # Persistent job manifest & checkpoints
            ├── chapters/      # Standalone HTML chapter files
            ├── images/        # Downloaded illustrations & images
            ├── conversion/
            │   └── staging/   # Staged XHTML chapter artifacts
            └── output/
                └── *.epub     # Final packaged EPUB book
```

---

## Windows Setup Instructions

### Prerequisites
- Windows 10 / 11
- Python 3.11 or Python 3.12 installed and added to your `PATH`.

### 1. Create a Virtual Environment
Open PowerShell or Command Prompt in the project directory:

```powershell
python -m venv .venv
```

### 2. Activate the Virtual Environment
In PowerShell:
```powershell
.venv\Scripts\Activate.ps1
```
*(If PowerShell displays an execution policy error, run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` first).*

Or in standard Command Prompt (`cmd.exe`):
```cmd
.venv\Scripts\activate.bat
```

### 3. Install Dependencies
```powershell
pip install -r requirements.txt
```

---

## Running the Application

Launch the Streamlit web interface:

```powershell
.venv\Scripts\streamlit run app.py
```

Your default web browser will open automatically at `http://localhost:8501`.

---

## How Chapter Discovery and Validation Work

1. **User Provides Starting Chapter URL**:
   Flamecomics uses arbitrary hex identifiers in chapter URLs (e.g. `https://flamecomics.xyz/novel/8/580e7d866052dfd8`). The application **never** attempts to increment numbers in URLs.
2. **Dynamic Next-Chapter Navigation**:
   For every chapter, the scraper:
   - Fetches the HTML.
   - Discovers the next chapter link dynamically from Next.js page props (`props.pageProps.next`) or DOM navigation links (`<button rel="next chapter">`, `<a rel="next">`).
   - Resolves relative links safely.
3. **Authoritative Chapter Identification**:
   - The chapter number is extracted directly from the chapter heading (e.g., `Chapter 166: Episode 32 — Kim Dokja's Love (3)`), page title, or metadata.
   - Discovery order is recorded in the manifest.
   - The archiver detects repeated URLs (navigation loops), duplicate chapter numbers, and unexpected gaps.
   - Archival stops automatically once the requested ending chapter number is completed.

---

## Job Lifecycle: Pause, Resume, and Crash Recovery

### Starting a New Job
1. In the **"🚀 Start New Archival Job"** tab:
   - Enter the Starting Chapter URL (e.g. `https://flamecomics.xyz/novel/8/580e7d866052dfd8`).
   - Enter the Ending Chapter Number (e.g. `10.0`).
   - Click **"🔍 Inspect Starting Chapter"** to verify that Flamecomics is reachable and confirm the starting title before beginning.
   - Click **"🚀 Start Archival Job"**.
2. The job runs inside an asynchronous background thread, allowing the Streamlit UI to remain responsive.

### Pausing and Resuming
- **Pause**: Click the **"⏸️ Pause"** button at any time. The background worker cleanly finishes the current active chapter or asset, writes an atomic checkpoint to `manifest.json`, and stops without corrupting files.
- **Resume**: Re-open the application or navigate to **"📂 Existing Jobs & Conversion"** and click **"▶️ Resume Archival"**. The archiver inspects existing completed chapters on disk and resumes from the next chapter without redownloading finished work.

### Recovery After Application Crash or Restart
- Job manifests are saved atomically (`manifest.json.tmp` -> `manifest.json`).
- When an existing job is loaded, `reconcile_state_from_disk()` automatically inspects saved files. Any chapters or assets marked `in_progress` during an unexpected shutdown are verified against actual disk contents. If valid, they are marked completed; otherwise they are safely queued for retry.

### Retrying Failed Chapters and Missing Images
- If network timeouts or transient errors cause a chapter or image to fail, click **"🔄 Retry Failed"**.
- Only failed chapters and missing images are retried; already completed chapters are never re-downloaded.

---

## Phase 2: Offline EPUB Conversion

The EPUB converter operates **entirely offline** on the local files saved in `data/jobs/<job_id>/`:
1. **Validate Archive**: Click **"🔍 Validate Archive"** to run an offline integrity audit verifying chapter continuity, HTML readability, and image presence.
2. **Convert to EPUB**: Click **"📚 Convert to EPUB"**.
   - Chapters are normalized and staged as XHTML in `conversion/staging/`.
   - Staging is resumable: if interrupted, previously staged chapters are reused.
   - EbookLib packages the book with standard EPUB 3 navigation, table of contents, and inline illustrations positioned exactly between paragraphs as in the source.
   - The file is written to `output/ORV_Ch<start>-<end>.epub`.
3. **Generate Anyway Override**: If an archive is incomplete (e.g., missing a chapter or image), conversion is blocked by default. Check **"⚠️ Generate Anyway"** to override the warning and compile an EPUB with the available material.
4. **Download**: A download button is provided directly in the interface to save the generated `.epub` file.

---

## Running Automated Tests

All tests run completely offline using mocked HTTP calls and HTML fixtures:

```powershell
.venv\Scripts\pytest -v
```

The test suite covers:
- Chapter title and number extraction.
- Discovery of relative and absolute next links.
- Loop detection, missing next links, and duplicate chapter numbers.
- Base64 data URI decoding and HTTP image downloads.
- Atomic manifest writing and recovery from stale `in_progress` state.
- Archive validation and the "Generate Anyway" override.
- EPUB ZIP packaging, table of contents, and embedded image inspection.
- Cooperative pause/resume and retry handling.

---

## Known Limitations

- **DOM Structure Changes**: If Flamecomics significantly changes its page layout or removes `__NEXT_DATA__`, the fallback selectors in `src/parser.py` (`find_content_container`) will continue to locate the largest text block. Selectors are isolated in `src/parser.py` to allow easy maintenance.
- **Cloudflare / Anti-Bot Challenges**: The application uses polite rate limiting and standard browser headers. If Flamecomics enables aggressive CAPTCHAs or Cloudflare Turnstile barriers, direct automated scraping may be restricted.
