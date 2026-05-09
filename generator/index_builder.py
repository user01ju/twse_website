import json
import re
from pathlib import Path

from config import REPORTS_DIR, OUTPUT_DIR
from generator import renderer


def _read_generated_at(html_path: Path) -> str:
    """Extract generated_at timestamp from bottom of report HTML."""
    try:
        text = html_path.read_text(encoding="utf-8")
        m = re.search(r"產生時間：([^<]+)", text)
        return m.group(1).strip() if m else "—"
    except Exception:
        return "—"


def rebuild() -> list[dict]:
    """Scan reports dir, build manifest, regenerate index.html."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    report_files = sorted(REPORTS_DIR.glob("????-??-??.html"), reverse=True)
    reports = []
    for f in report_files:
        date_str = f.stem
        reports.append({
            "date":         date_str,
            "generated_at": _read_generated_at(f),
        })

    renderer.render_index(reports, OUTPUT_DIR / "index.html")
    return reports
