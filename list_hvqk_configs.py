"""List all hvqk waterfall config files grouped by module."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Tuple

ROOT = Path(__file__).parent
MODULES_DIR = ROOT / "Test Programs" / "PTUSDJXA1H21G402546" / "Modules"
TARGET_SUFFIX = "hvqk.config.json"


def find_module_hvqk_files(modules_dir: Path) -> List[Tuple[str, List[str]]]:
    """Return a list of (module_name, [filenames]) for hvqk.config json files."""
    if not modules_dir.is_dir():
        raise FileNotFoundError(f"Modules directory not found: {modules_dir}")

    results: List[Tuple[str, List[str]]] = []
    for module_dir in sorted(p for p in modules_dir.iterdir() if p.is_dir()):
        input_dir = module_dir / "InputFiles"
        if not input_dir.is_dir():
            continue

        matches = sorted(
            file_path.name
            for file_path in input_dir.rglob("*")
            if file_path.is_file() and file_path.name.endswith(TARGET_SUFFIX)
        )
        if matches:
            results.append((module_dir.name, matches))
    return results


def print_report(entries: Iterable[Tuple[str, List[str]]]) -> None:
    for module_name, filenames in entries:
        print(f"{module_name} HVQK waterfall files:")
        for filename in filenames:
            print(filename)
        print()


def main() -> None:
    entries = find_module_hvqk_files(MODULES_DIR)
    if not entries:
        print("No hvqk.config.json files were found under the Modules directory.")
        return

    print_report(entries)


if __name__ == "__main__":
    main()
