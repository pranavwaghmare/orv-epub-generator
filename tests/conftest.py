import base64
import io
import json
import sys
from pathlib import Path
import pytest
from PIL import Image

# Ensure root workspace directory is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def create_tiny_png_bytes(color: tuple = (255, 0, 0)) -> bytes:
    """Generate a tiny 10x10 PNG image in bytes."""
    img = Image.new("RGB", (10, 10), color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def tiny_png_bytes() -> bytes:
    return create_tiny_png_bytes()


@pytest.fixture
def tiny_png_b64() -> str:
    raw = create_tiny_png_bytes()
    return f"data:image/png;base64,{base64.b64encode(raw).decode('utf-8')}"


@pytest.fixture
def flamecomics_ch1_html(tiny_png_b64: str) -> str:
    """Mock Flamecomics chapter with __NEXT_DATA__ and Mantine CSS modules."""
    next_data = {
        "props": {
            "pageProps": {
                "chapter": {
                    "novel_id": 8,
                    "chapter": "1.00",
                    "chapter_title": "Prologue - Three Ways to Survive the Apocalypse",
                    "token": "580e7d866052dfd8",
                },
                "next": "db6ecd64465f6b07",
                "previous": None,
            }
        }
    }
    json_str = json.dumps(next_data)
    return f"""<!DOCTYPE html>
<html>
<head>
    <title>Omniscient Reader's Viewpoint - Chapter 1 - Flame Comics</title>
</head>
<body>
    <div class="Header-module__nav">Flame Comics Header <button>Login</button></div>
    <p class="TopChapterNavbar-module__chapter_title">Chapter 1</p>
    <div class="chapterContent-module__hRMtSq__chapterContent">
        <p>Chapter 1: Prologue - Three Ways to Survive the Apocalypse</p>
        <p>There are three ways to survive in a ruined world.</p>
        <img src="{tiny_png_b64}" alt="Prologue Illustration" />
        <p>Now, I have forgotten a few, but one thing is certain.</p>
        <div class="reaction-box">Reactions: 100 likes</div>
        <script>console.log('tracker');</script>
    </div>
    <div class="comments-section">Comments here</div>
    <button rel="next chapter">Next Chapter</button>
    <script id="__NEXT_DATA__" type="application/json">{json_str}</script>
</body>
</html>
"""


@pytest.fixture
def flamecomics_ch2_html() -> str:
    """Mock Flamecomics chapter 2 ending link (no next chapter)."""
    next_data = {
        "props": {
            "pageProps": {
                "chapter": {
                    "novel_id": 8,
                    "chapter": "2.00",
                    "chapter_title": "Episode 1 - Launch of the Paywall (1)",
                    "token": "db6ecd64465f6b07",
                },
                "next": None,  # End of available chapters
                "previous": "580e7d866052dfd8",
            }
        }
    }
    json_str = json.dumps(next_data)
    return f"""<!DOCTYPE html>
<html>
<head>
    <title>Omniscient Reader's Viewpoint - Chapter 2 - Flame Comics</title>
</head>
<body>
    <p class="TopChapterNavbar-module__chapter_title">Chapter 2</p>
    <div class="chapterContent-module__hRMtSq__chapterContent">
        <p>Chapter 2: Episode 1 - Launch of the Paywall (1)</p>
        <p>“I’m Dokja.”</p>
        <p>This was how I often introduced myself to people.</p>
    </div>
    <script id="__NEXT_DATA__" type="application/json">{json_str}</script>
</body>
</html>
"""


@pytest.fixture
def generic_html_chapter() -> str:
    """Standard generic HTML chapter with relative next link."""
    return """<!DOCTYPE html>
<html>
<head>
    <title>Chapter 10: The Encounter</title>
</head>
<body>
    <div class="chapter-content">
        <h1>Chapter 10: The Encounter</h1>
        <p>First paragraph of chapter ten.</p>
        <img src="/assets/ch10_art.png" alt="Scene" />
        <p>Second paragraph after illustration.</p>
    </div>
    <a href="/novel/8/ch11" rel="next">Next Chapter</a>
</body>
</html>
"""
