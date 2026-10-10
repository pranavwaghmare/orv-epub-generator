# Omniscient Reader's Viewpoint (ORV) — EPUB Maker

Archive chapters of *Omniscient Reader's Viewpoint* from Flamecomics and generate clean, beautifully formatted EPUBs with illustrations, interactive footnotes, and styled system windows for your e-reader or phone.

---

## ⚡ Quick Start

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
*(If you see a PowerShell script execution error, run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` and try again).*

### 3. Launch the Web App
```powershell
.venv\Scripts\streamlit run app.py
```
Your browser will open automatically at **`http://localhost:8501`**.

---

## 📖 How to Make Your EPUB

1. **Start Archiving**:
   - In the **Start New Archival Job** tab, paste your starting chapter URL (e.g. `https://flamecomics.xyz/novel/8/580e7d866052dfd8`).
   - Enter your ending chapter number (e.g. `50`).
   - Click **🚀 Start Archival Job**.
   - *Note: If any of those chapters were already archived in an earlier job, the app automatically copies them instantly without re-downloading!*

2. **Convert to EPUB**:
   - Go to the **Existing Jobs & Conversion** tab.
   - Click **📚 Convert to EPUB**.
   - When finished, click **📥 Download EPUB** to save your book!

---

## ✨ Clean Reading Experience

* **No Wasted Space**: Translator banners, repetitive credit blocks, and website dividing lines are automatically removed.
* **Tight, Reflowable Typography**: Normal, comfortable spacing between paragraphs, illustrations, and status boxes.
* **In-World System Windows**: Attributes, scenarios, and notifications are formatted into distinct, styled status boxes.
* **Interactive Footnotes**: Clickable references (`[1]`) that jump to footnotes with a return link (`↩`) to bring you right back to where you were reading.
* **Episode-Wise Table of Contents**: Chapters are grouped neatly by their story arc (Prologue, Episode 1, Episode 2, etc.).
* **Official Cover**: Automatically embeds the book cover so it looks great in your e-reader library.

---

## 🧪 Testing

To run the automated test suite offline:
```powershell
.venv\Scripts\pytest -v
```
