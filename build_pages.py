"""Export a credential-free GitHub Pages preview from real offline SDK runs.

Python runs only while building the site. The published browser replays these
verified results; editable Python and optional live API calls run in Colab.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import shutil
import tempfile
from pathlib import Path
from urllib.parse import quote

import lab
import notebook_lab
import tutorial_series

ROOT = Path(__file__).resolve().parent
MODULE_IDS = ("why-tool-runner", "build-tools", "verify-actions", "make-it-yours")
NOTEBOOK_PATHS = frozenset(
    f"/tutorials/{index:02d}-{module}{suffix}.ipynb"
    for index, module in enumerate(MODULE_IDS, 1)
    for suffix in ("", ".reference")
)
TOP_LEVEL = frozenset(("index.html", "styles.css", "app.js", "static-data.js", ".nojekyll"))


def validate_target(repository: str, ref: str) -> None:
    """Accept an owner/repository and an unambiguous branch, tag, or commit."""
    if not isinstance(repository, str) or not re.fullmatch(
        r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?/[A-Za-z0-9][A-Za-z0-9._-]{0,99}", repository
    ) or ".." in repository or repository.endswith(".git"):
        raise ValueError("Use a GitHub owner/repository, such as dannymsullivan/tool-runner-lab.")
    if not isinstance(ref, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}", ref) or ".." in ref or ref.endswith("."):
        raise ValueError("Use a simple branch, tag, or commit, such as main.")


def tutorial_catalog(repository: str, ref: str) -> dict:
    validate_target(repository, ref)
    catalog = copy.deepcopy(tutorial_series.manifest())
    if tuple(module.get("id") for module in catalog.get("modules", [])) != MODULE_IDS:
        raise ValueError("The four expected tutorial modules must be present in order.")
    found = set()
    for index, module in enumerate(catalog["modules"], 1):
        for field, suffix, colab_field in (("notebook", "", "colab_url"), ("reference", ".reference", "reference_colab_url")):
            expected = f"/tutorials/{index:02d}-{module['id']}{suffix}.ipynb"
            if module.get(field) != expected or expected not in NOTEBOOK_PATHS:
                raise ValueError("Tutorial downloads must match the eight declared notebooks.")
            source = ROOT / expected.lstrip("/")
            if source.is_symlink() or not source.is_file() or source.resolve().parent != (ROOT / "tutorials").resolve():
                raise ValueError("Each declared notebook must be a regular file in tutorials/.")
            notebook = json.loads(source.read_text(encoding="utf-8"))
            if notebook.get("nbformat") != 4 or not notebook.get("cells"):
                raise ValueError("Each published notebook must contain valid notebook cells.")
            if any(cell.get("outputs") or cell.get("execution_count") is not None for cell in notebook["cells"] if cell.get("cell_type") == "code"):
                raise ValueError("Publish clean notebooks without saved execution output.")
            module[colab_field] = f"https://colab.research.google.com/github/{repository}/blob/{quote(ref, safe='')}{expected}"
            found.add(expected)
    if found != NOTEBOOK_PATHS:
        raise ValueError("All eight notebooks must be available.")
    return catalog


def build_payload(repository: str, ref: str) -> dict:
    """Execute fixed practice examples with the SDK's in-memory transport."""
    catalog = tutorial_catalog(repository, ref)
    config = lab.get_config()
    # Never export environment-derived model names, connection state, or keys.
    config.update(
        live_available=False, connected=False, models=[], connection_source=None,
        model_access_verified=False, default_model=lab.DEFAULT_MODEL,
        source_url=f"https://github.com/{repository}", source_label="View source",
        source_notice="Source code and runnable Colab notebooks.", static_preview=True,
        fixture_notice="This Pages preview replays verified results recorded from the real SDK, Python tools, and SQLite. Run the code yourself in Colab.",
        live_notice="Optional live Claude runs are available inside the Colab notebooks.",
    )
    runs = {}
    for count in (0, 3):
        result = lab.run_lab(
            {"mode": "fixture", "scenario": "normal", "inventory_count": count},
            credentials={"api_key": "", "model": lab.DEFAULT_MODEL},
        )
        if not result.get("completed") or result.get("stop_reason") != "end_turn" or not result.get("checks") or not all(check["passed"] for check in result["checks"]):
            raise ValueError(f"The stock-{count} preview did not pass its real execution checks.")
        returns = result["state"]["returns"]
        if len(returns) != (1 if count == 0 else 0):
            raise ValueError("The recorded preview has an unexpected database result.")
        # These are replays, not measurements from the viewer's browser. Strip
        # build-machine wall time and random IDs to make exports reproducible.
        result["run_id"] = f"recorded-stock-{count}"
        result["metrics"]["duration_ms"] = 0
        for event in result["events"]:
            event["elapsed_ms"] = 0
        runs[str(count)] = result
    notebook = notebook_lab.curriculum()
    cells = {}
    for cell in notebook["cells"] + notebook["deep_cells"]:
        for variant in cell.get("variants", [None]):
            variant_id = variant["id"] if variant else None
            result = notebook_lab.run_cell(cell["id"], variant_id)
            if not result.get("passed") or not result.get("output") or result.get("cell_id") != cell["id"]:
                raise ValueError("A recorded notebook cell failed its execution check.")
            key = cell["id"] + (f":{variant_id}" if variant_id else "")
            cells[key] = result
    if len(cells) != 15:
        raise ValueError("Expected five introductory cells and ten experiment variants.")
    return {"config": config, "notebook": notebook, "tutorials": catalog, "runs": runs, "cells": cells}


def export_pages(repository: str, ref: str = "main", output: str | Path = "docs") -> Path:
    """Write only approved static assets; reject foreign files at the target."""
    validate_target(repository, ref)
    requested = Path(output)
    if requested.is_symlink():
        raise ValueError("The export destination must not be a symbolic link.")
    destination = requested.resolve()
    protected = (ROOT / "web", ROOT / "tutorials")
    if destination == ROOT or destination in ROOT.parents or any(destination == source or source in destination.parents for source in protected):
        raise ValueError("The export directory must be separate from the lab sources.")
    allowed = TOP_LEVEL | {path.lstrip("/") for path in NOTEBOOK_PATHS}
    if destination.exists():
        if not destination.is_dir():
            raise ValueError("The export destination must be a directory.")
        for path in destination.rglob("*"):
            relative = path.relative_to(destination).as_posix()
            if path.is_symlink() or (path.is_file() and relative not in allowed) or (path.is_dir() and relative != "tutorials"):
                raise ValueError("The export destination contains files not owned by this generator.")
    payload = build_payload(repository, ref)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="tool-runner-pages-", dir=destination.parent) as temporary:
        stage = Path(temporary)
        (stage / "tutorials").mkdir()
        for filename in ("styles.css", "app.js"):
            shutil.copyfile(ROOT / "web" / filename, stage / filename)
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        html = html.replace('<html lang="en">', '<html lang="en" data-hosting="pages">')
        html = re.sub(r'\b(href|src)="(/[^\"]*)"', lambda match: f'{match[1]}=".{match[2]}"', html)
        app_script = '<script src="./app.js" defer></script>'
        if html.count(app_script) != 1:
            raise ValueError("Expected one deferred app.js script in the page.")
        html = html.replace(app_script, '<script src="./static-data.js"></script>\n  ' + app_script)
        (stage / "index.html").write_text(html, encoding="utf-8")
        encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        (stage / "static-data.js").write_text("window.LAB_STATIC = " + encoded + ";\n", encoding="utf-8")
        (stage / ".nojekyll").write_text("", encoding="utf-8")
        for path in NOTEBOOK_PATHS:
            shutil.copyfile(ROOT / path.lstrip("/"), stage / path.lstrip("/"))
        destination.mkdir(parents=True, exist_ok=True)
        for path in stage.rglob("*"):
            if path.is_file():
                target = destination / path.relative_to(stage)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True, help="GitHub OWNER/REPO that contains tutorials/.")
    parser.add_argument("--ref", default="main", help="Published branch, tag, or commit (default: main).")
    parser.add_argument("--output", type=Path, default=Path("docs"), help="Generated site directory (default: docs).")
    args = parser.parse_args()
    output = export_pages(args.repository, args.ref, args.output)
    print(f"Exported verified Pages preview to {output}")


if __name__ == "__main__":
    main()
