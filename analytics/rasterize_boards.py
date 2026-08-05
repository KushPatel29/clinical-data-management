"""
Render the SVG status boards to PNG.

GitHub renders an SVG in a README only when it is referenced as an image and
served from the repository, and it strips the CSS that would style it — so the
boards are committed as both. The SVG is the artefact the tests assert against;
the PNG is what appears at the top of the README.

Headless Chrome rather than a Python imaging library, for the same reason the
boards are hand-drawn SVG in the first place: rasterising a chart is not a good
enough reason to take on cairosvg and its native dependencies. Chrome is already
on any machine that will look at this repository, and if it is not, this script
says so and exits without failing a build.

    python analytics/rasterize_boards.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

BOARDS = [
    ("dm_status_board.svg", 1180, 660),
    ("warehouse_board.svg", 1180, 700),
]

CANDIDATES = [
    "chrome", "google-chrome", "chromium", "chromium-browser", "msedge",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]


def find_browser() -> str | None:
    for candidate in CANDIDATES:
        found = shutil.which(candidate) if not Path(candidate).is_absolute() else candidate
        if found and Path(found).exists():
            return found
    return None


def render(browser: str, svg: Path, width: int, height: int) -> Path:
    png = svg.with_suffix(".png")
    with tempfile.TemporaryDirectory() as profile:
        subprocess.run(
            [
                browser,
                "--headless=new",
                "--disable-gpu",
                # Without a fresh profile, a running Chrome takes over the
                # command line and this silently opens a tab instead.
                f"--user-data-dir={profile}",
                f"--window-size={width},{height}",
                "--default-background-color=00000000",
                "--virtual-time-budget=4000",
                f"--screenshot={png}",
                svg.resolve().as_uri(),
            ],
            check=True, capture_output=True, timeout=120,
        )
    return png


def main() -> int:
    browser = find_browser()
    if browser is None:
        print("no Chrome or Edge found; leaving the committed PNGs alone")
        return 0

    for name, width, height in BOARDS:
        svg = DOCS / name
        if not svg.exists():
            print(f"  skipped {name} (not generated)")
            continue
        try:
            png = render(browser, svg, width, height)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            print(f"  failed {name}: {exc}", file=sys.stderr)
            return 1
        print(f"  wrote {png.relative_to(ROOT)} ({png.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
