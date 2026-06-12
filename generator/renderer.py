import re
import shutil
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

from config import TEMPLATES_DIR, STATIC_SRC, STATIC_OUT


def _ai_summary_html(text: str) -> Markup:
    """Convert structured AI summary text to HTML sections.

    Expected input format:
        【大盤概況】
        一段話 ...

        【熱門族群】
        • 族群A：...
        • 族群B：...

    Output: one <div class="ai-section"> per 【block】, arranged in a 2-col grid.
    """
    # Split on 【...】 boundaries while keeping the header
    blocks = re.split(r'(?=【)', text.strip())
    sections = []

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        lines = block.splitlines()
        header_match = re.match(r'^【(.+?)】', lines[0].strip())
        title = escape(header_match.group(1)) if header_match else None
        body_lines = lines[1:] if header_match else lines

        inner = []
        bullet_buf = []

        def flush_bullets():
            if bullet_buf:
                _strip_bullet = re.compile(r"^\s*[•\-\*]\s*")
                items = "".join(
                    f"<li>{escape(_strip_bullet.sub('', l))}</li>"
                    for l in bullet_buf
                )
                inner.append(f"<ul>{items}</ul>")
                bullet_buf.clear()

        for line in body_lines:
            line = line.strip()
            if not line:
                continue
            if re.match(r'^[•\-\*]', line):
                bullet_buf.append(line)
            else:
                flush_bullets()
                inner.append(f"<p>{escape(line)}</p>")
        flush_bullets()

        head_html = f'<div class="ai-section-head">{title}</div>' if title else ""
        sections.append(f'<div class="ai-section">{head_html}{"".join(inner)}</div>')

    return Markup("\n".join(sections))


def _make_env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "j2"]),
    )
    env.globals["enumerate"] = enumerate
    env.filters["ai_summary_html"] = _ai_summary_html
    return env


_env = None


def get_env() -> Environment:
    global _env
    if _env is None:
        _env = _make_env()
    return _env


def render_report(target_date: date, sections: dict, output_path: Path, generated_at: str) -> None:
    env = get_env()
    tmpl = env.get_template("report.html")
    html = tmpl.render(
        date_str=target_date.isoformat(),
        sections=sections,
        generated_at=generated_at,
        asset_prefix="../",
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")


def render_index(reports: list[dict], output_path: Path) -> None:
    env = get_env()
    tmpl = env.get_template("index.html.j2")
    html = tmpl.render(
        reports=reports,
        asset_prefix="",
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")


def copy_static() -> None:
    if STATIC_OUT.exists():
        shutil.rmtree(STATIC_OUT)
    shutil.copytree(str(STATIC_SRC), str(STATIC_OUT))
