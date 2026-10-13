# Omniscient Reader's Viewpoint (ORV) — EPUB Maker

Archive chapters of *Omniscient Reader's Viewpoint* from **WeTriedTLS** or **Flamecomics** and generate clean, beautifully formatted EPUBs with complete illustrations, interactive footnotes, dedicated title pages, and styled system windows for your e-reader or phone.

---

## ⚡ Features

* **Dual-Source Support with Image Enrichment**:
  * **WeTriedTLS** (*Recommended*): Complete text with all original novel illustrations (`https://wetriedtls.com/series/omniscient-readers-viewpoint/chapter-1`).
  * **Flamecomics**: Scraped text with automatic image enrichment from WeTriedTLS—ensuring no illustrations are missed even if the Flamecomics release omitted them.
* **Main Story & Side Story (Sequel) Support**:
  * **Chapters 1–551**: Formatted as *Omniscient Reader's Viewpoint* with the original cover (`cover.webp`).
  * **Chapters 552+ (Sequel)**: Automatically switches title to *Omniscient Reader's Viewpoint: Side Story* with the dedicated sequel cover (`coverss.jpg`) and matched title page branding.
* **Premium Reading Experience & Typography**:
  * **Dedicated Title Page**: Elegant standalone title page placed before the Table of Contents.
  * **Interactive Footnotes**: Clickable superscript references (`[1]`) linking directly to chapter notes, with return links (`↩`) to resume reading. Proper word boundary spacing around bold text is automatically preserved.
  * **Styled System Windows**: In-world attribute windows, scenario notifications, constellation messages, and skill dialogues are converted into clean status boxes.
  * **Polished Scene Breaks & Divider Cleanup**: Converts raw asterisks (`* * *`) or dividing dashes into uniform diamond scene breaks (`◆ ◆ ◆`). Strips duplicate chapter headers, translator banners, and extraneous white lines.
  * **Structured Table of Contents**: Chapters are automatically categorized by story arc (Prologue, Episode 1, Episode 2, etc.).
* **Offline Job Archiver & Instant Reuse**:
  * Fully resumable offline downloads with raw HTML, manifest metadata, and downloaded images stored locally.
  * Chapters previously archived in earlier jobs are copied instantly without re-downloading.
* **Clean Web Interface**:
  * Built with Streamlit for easy job configuration, live progress logging, archive inspection, and one-click EPUB generation and download.

---

## 🚀 Quick Start

### 1. Requirements
* Windows 10 or 11
* Python 3.11 or 3.12 installed ([python.org](https://www.python.org/downloads/))

### 2. Setup (run in PowerShell)
```powershell
# Open project folder and create virtual environment
python -m venv .venv
.venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt
```
*(If you encounter a PowerShell script execution policy error, run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` and try again).*

### 3. Launch the Web App
```powershell
.venv\Scripts\streamlit run app.py
```
Your browser will open automatically at **`http://localhost:8501`**.

---

## 📖 How to Make Your EPUB

1. **Start Archiving**:
   - In the **Start New Archival Job** tab, select a **Predefined Volume / Scope Preset** or customize:
     * **Omnibus**: Chapters 1–551 (Complete Main Story)
     * **Volume 1 (Part 1)**: Chapters 1–188 (Episodes 1–35)
     * **Volume 2 (Part 2)**: Chapters 189–284 (Episodes 36–52)
     * **Volume 3 (Part 3)**: Chapters 285–398 (Episodes 53–75)
     * **Volume 4 (Part 4)**: Chapters 399–486 (Episodes 76–92)
     * **Volume 5 (Part 5)**: Chapters 487–551 (Episodes 93–99 + Epilogue)
     * **Custom Range**: Any custom URL and ending chapter number.
   - Click **🚀 Start Archival Job**.
   - *Note: Any chapter already archived in an earlier job is copied instantly from local cache without re-downloading.*

2. **Convert to EPUB (Full or Sub-Ranges)**:
   - Switch to the **Existing Jobs & Conversion** tab and select your job.
   - Choose your **Conversion Scope**:
     * **All Archived Chapters**: Bundles all chapters in the job into a single volume/omnibus.
     * **Custom Sub-Chapter Range**: Specify any starting and ending chapters (e.g. Ch 1–188, Ch 517–551) to generate a targeted volume.
   - Click **📚 Convert to EPUB**.
   - Download any generated EPUB directly from the **📥 Generated EPUBs** list to transfer to your e-reader or reading app (Apple Books, KOReader, Kindle, Moon+ Reader, etc.).

---

## 🛠️ Project Architecture

```
orv-flamecomics/
├── app.py                     # Streamlit web application & UI
├── requirements.txt           # Python package dependencies
├── pytest.ini                 # Pytest configuration
├── cover.webp                 # Main story cover (Chapters 1–551)
├── coverss.jpg                # Side story / sequel cover (Chapters 552+)
├── src/
│   ├── scraper.py             # Web scraping for WeTriedTLS & Flamecomics
│   ├── parser.py              # HTML parsing, metadata & image enrichment
│   ├── chapter_refiner.py     # Content sanitization, scene breaks & system windows
│   ├── epub_converter.py      # EPUB3 builder, cover logic & TOC hierarchy
│   ├── downloader.py          # Parallel image downloader & caching
│   ├── job_manager.py         # Archival job persistence & lifecycle
│   ├── manifest.py            # Chapter manifest data models
│   └── archive_validator.py   # Job integrity & completeness verification
├── tests/                     # Comprehensive offline test suite (50 tests)
└── data/jobs/                 # Local directory for archived jobs & EPUBs
```

---

## 🧪 Testing

The project includes automated offline unit and integration tests covering scrapers, parsers, HTML refiners, covers, and EPUB generation:

```powershell
.venv\Scripts\pytest -v
```
