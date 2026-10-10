"""Static checks of the Vercel shape (BUILD_ALL C2; TEST_PLAN VERCEL-06, VERCEL-07, VERCEL-08)."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

from tests.harness.tags import tid

API_DIR = Path(__file__).resolve().parents[1]
REPO = API_DIR.parents[1]


def _config() -> dict:  # type: ignore[type-arg]
    data: dict = json.loads((REPO / "vercel.json").read_text(encoding="utf-8"))  # type: ignore[type-arg]
    return data


@tid("VERCEL-07")
def test_VERCEL_07_one_project_two_services_same_origin() -> None:
    cfg = _config()
    assert set(cfg["services"]) == {"web", "api"}
    assert cfg["services"]["web"]["root"] == "apps/web/" and cfg["services"]["web"]["framework"] == "nextjs"
    api = cfg["services"]["api"]
    assert api["root"] == "apps/api/" and api["framework"] == "fastapi"
    module, _, attr = api["entrypoint"].partition(":")
    assert (API_DIR / Path(*module.split("."))).with_suffix(".py").exists() and attr == "app"
    rewrites = [(r["source"], r["destination"]["service"]) for r in cfg["rewrites"]]
    # Order matters: the two Blob routes are Next.js routes under /api (upload token, signed view);
    # then the API; then the web.
    assert rewrites == [
        ("/api/blob-upload", "web"),
        ("/api/blob-view", "web"),
        ("/api/(.*)", "api"),
        ("/(.*)", "web"),
    ]
    for key in ("functions", "buildCommand", "installCommand", "framework", "outputDirectory"):
        assert key not in cfg, f"{key} isn't valid at the top level in services mode"
    [cron] = cfg["crons"]
    assert cron["path"] == "/api/v1/cron/daily"
    minute, hour, *rest = cron["schedule"].split()
    assert rest == ["*", "*", "*"] and minute.isdigit() and hour.isdigit(), "Hobby: at most once a day"


@tid("VERCEL-07")
def test_VERCEL_07_queue_subscriber_is_declared_in_the_api_service() -> None:
    project = tomllib.loads((API_DIR / "pyproject.toml").read_text(encoding="utf-8"))
    subscribers = project["tool"]["vercel"]["subscribers"]
    assert [s["entrypoint"] for s in subscribers] == ["app.queues.outbox"]
    assert (API_DIR / "app" / "queues" / "outbox.py").exists()


@tid("VERCEL-06")
def test_VERCEL_06_alembic_never_loads_with_the_request_app() -> None:
    """Importing the deployed entrypoint must not import Alembic (migrations run in `make deploy`)."""
    code = (
        "import os, sys\n"
        "os.environ.setdefault('APP_ENV', 'test')\n"
        "import app.main\n"
        "import app.queues.outbox\n"
        "bad = sorted(m for m in sys.modules if m == 'alembic' or m.startswith('alembic.'))\n"
        "print(','.join(bad))\n"
    )
    out = subprocess.run(  # noqa: S603 - our own interpreter and a fixed script
        [sys.executable, "-c", code], cwd=API_DIR, capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "", f"alembic imported by the request app: {out.stdout}"
    for path in (API_DIR / "app").rglob("*.py"):
        if "tools" in path.parts:
            continue  # operator tools (restore drill) run from the command line only
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"^\s*(from|import) alembic", text, re.M), f"{path} imports alembic"


HEAVY = ("tesseract", "pytesseract", "opencv", "cv2", "torch", "easyocr", "paddle", "onnxruntime", "tensorflow")


@tid("VERCEL-08")
def test_VERCEL_08_python_bundle_small_and_no_ocr_libraries() -> None:
    """Runtime dependencies only (no dev group): no OCR or ML stacks, and well under 500 MB."""
    lock = tomllib.loads((API_DIR / "uv.lock").read_text(encoding="utf-8"))
    project = tomllib.loads((API_DIR / "pyproject.toml").read_text(encoding="utf-8"))
    names = {p["name"] for p in lock["package"]}
    assert not [n for n in names if any(h in n for h in HEAVY)], "OCR runs in the browser (ADR-0010)"
    runtime = [re.split(r"[<>=\[ ;]", d, maxsplit=1)[0].lower() for d in project["project"]["dependencies"]]
    site = Path(sys.prefix) / (
        "Lib" if sys.platform == "win32" else f"lib/python{sys.version_info[0]}.{sys.version_info[1]}"
    )
    site = site / "site-packages" if (site / "site-packages").exists() else site
    seen: set[str] = set()
    todo = list(runtime)
    by_name = {p["name"].lower(): p for p in lock["package"]}
    while todo:  # walk the locked dependency graph from the runtime roots
        name = todo.pop()
        if name in seen or name not in by_name:
            continue
        seen.add(name)
        todo += [d["name"].lower() for d in by_name[name].get("dependencies", [])]
        for extra in by_name[name].get("optional-dependencies", {}).values():
            todo += [d["name"].lower() for d in extra]
    total = 0
    for dist in site.glob("*.dist-info"):
        dist_name = dist.name.split("-")[0].lower().replace("_", "-")
        if dist_name not in seen:
            continue
        record = dist / "RECORD"
        if record.exists():
            for line in record.read_text(encoding="utf-8").splitlines():
                rel = line.split(",")[0]
                f = site / rel
                if f.is_file():
                    total += f.stat().st_size
    assert seen, "runtime dependency graph is empty"
    assert total < 250 * 1024 * 1024, f"runtime packages are {total // 2**20} MB; Vercel's limit is 500 MB"
