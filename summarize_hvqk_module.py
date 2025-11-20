"""Print the contents of every hvqk.config.json file for a given module."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent
MODULES_DIR = ROOT / "Test Programs" / "PTUSDJXA1H21G402546" / "Modules"
TARGET_SUFFIX = "hvqk.config.json"


def collect_module_files(module_name: str) -> list[Path]:
    module_dir = MODULES_DIR / module_name
    input_dir = module_dir / "InputFiles"
    if not module_dir.is_dir():
        raise FileNotFoundError(f"Module folder not found: {module_dir}")
    if not input_dir.is_dir():
        raise FileNotFoundError(
            f"Module '{module_name}' does not contain an InputFiles directory."
        )

    matches = sorted(
        file_path
        for file_path in input_dir.rglob("*")
        if file_path.is_file() and file_path.name.endswith(TARGET_SUFFIX)
    )
    return matches


def to_markdown_table(payload: dict[str, Any]) -> str:
    """Convert a JSON payload into a two-column markdown table."""

    lines = ["| Key | Value |", "| --- | --- |"]
    for key, value in payload.items():
        if isinstance(value, (dict, list)):
            formatted = json.dumps(value, ensure_ascii=False)
        else:
            formatted = str(value)
        formatted = formatted.replace("|", "\\|").replace("\n", "<br>")
        lines.append(f"| {key} | {formatted} |")
    return "\n".join(lines)


def print_module_summary(module_name: str, files: list[Path]) -> None:
    print(f"{module_name} HVQK waterfall config:")
    if not files:
        print("(No hvqk config files found)\n")
        return

    for file_path in files:
        print(file_path.name)
        with file_path.open(encoding="utf-8") as handle:
            data = json.load(handle)
        print(to_markdown_table(data))
        print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Print the full contents of hvqk config files for a module."
    )
    parser.add_argument("module", help="Module name (folder under Modules).")
    args = parser.parse_args()

    files = collect_module_files(args.module)
    print_module_summary(args.module, files)


if __name__ == "__main__":
    main()
