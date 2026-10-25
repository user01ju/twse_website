import shutil
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from config import TEMPLATES_DIR, STATIC_SRC, STATIC_OUT


def _make_env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "j2"]),
    )
    env.globals["enumerate"] = enumerate
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
