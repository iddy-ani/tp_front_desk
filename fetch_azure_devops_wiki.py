"""Utility to download Azure DevOps wiki sections.

This script authenticates with a Personal Access Token (PAT), traverses an Azure DevOps
Wiki section (including subpages), and stores each page as a Markdown file.
"""

import argparse
import base64
import os
import sys
from pathlib import Path
from typing import Iterable, Optional

import requests

API_VERSION = "7.0"
DEFAULT_ORG = "mit-is"
DEFAULT_PROJECT = "TorchWiki"
DEFAULT_WIKI = "TorchWiki.wiki"
DEFAULT_PAGE_PATH = "OTPL language and files"
DEFAULT_OUTPUT_DIR = "OTPL Wiki"
ENV_PAT_KEYS = ("TORCH_WIKI_READ_TOKEN", "AZDO_PAT")
INVALID_FILENAME_CHARS = set('<>:"/\\|?*')


def build_auth_header(pat: str) -> str:
    token = base64.b64encode(f":{pat}".encode("ascii")).decode("ascii")
    return f"Basic {token}"


def wiki_request(
    *,
    organization: str,
    project: str,
    wiki: str,
    page_path: str,
    pat: str,
    include_content: bool,
    recursion_level: Optional[str] = None,
) -> dict:
    url = f"https://dev.azure.com/{organization}/{project}/_apis/wiki/wikis/{wiki}/pages"
    params = {
        "path": page_path,
        "includeContent": "true" if include_content else "false",
        "api-version": API_VERSION,
    }
    if recursion_level:
        params["recursionLevel"] = recursion_level

    headers = {"Authorization": build_auth_header(pat)}
    response = requests.get(url, params=params, headers=headers, timeout=30)
    response.raise_for_status()
    return response.json()


def fetch_page_tree(
    *, organization: str, project: str, wiki: str, page_path: str, pat: str
) -> dict:
    return wiki_request(
        organization=organization,
        project=project,
        wiki=wiki,
        page_path=page_path,
        pat=pat,
        include_content=False,
        recursion_level="Full",
    )


def fetch_page_content(
    *, organization: str, project: str, wiki: str, page_path: str, pat: str
) -> dict:
    return wiki_request(
        organization=organization,
        project=project,
        wiki=wiki,
        page_path=page_path,
        pat=pat,
        include_content=True,
    )


def sanitize_segment(segment: str) -> str:
    cleaned = segment.strip()
    if not cleaned:
        return "untitled"
    result = []
    for char in cleaned:
        result.append("_" if char in INVALID_FILENAME_CHARS else char)
    sanitized = "".join(result).rstrip(".")
    return sanitized or "untitled"


def relative_parts(page_path: str) -> Iterable[str]:
    stripped = page_path.strip("/")
    if not stripped:
        return ("root",)
    return tuple(sanitize_segment(part) for part in stripped.split("/"))


def page_to_file_path(dest_dir: Path, page_path: str) -> Path:
    parts = list(relative_parts(page_path))
    if not parts:
        parts = ["root"]
    if len(parts) == 1:
        dirs: Iterable[str] = ()
        leaf = parts[0]
    else:
        dirs = parts[:-1]
        leaf = parts[-1]
    file_dir = dest_dir.joinpath(*dirs)
    file_dir.mkdir(parents=True, exist_ok=True)
    return file_dir / f"{leaf}.md"


def save_page(content: str, output_path: Path) -> None:
    output_path.write_text(content, encoding="utf-8")


def collect_paths(node: dict) -> Iterable[str]:
    path = node.get("path")
    if path:
        yield path
    for child in node.get("subPages") or []:
        yield from collect_paths(child)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download Azure DevOps wiki page content.")
    parser.add_argument("--organization", default=DEFAULT_ORG, help="Azure DevOps organization name")
    parser.add_argument("--project", default=DEFAULT_PROJECT, help="Azure DevOps project name")
    parser.add_argument("--wiki", default=DEFAULT_WIKI, help="Wiki identifier (e.g., TorchWiki.wiki)")
    parser.add_argument("--page-path", default=DEFAULT_PAGE_PATH, help="Wiki section root path")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="Directory to store Markdown files")
    parser.add_argument("--pat", help="Azure DevOps PAT. Defaults to env vars if omitted.")
    return parser.parse_args()


def resolve_pat(cli_pat: Optional[str]) -> Optional[str]:
    if cli_pat:
        return cli_pat
    for key in ENV_PAT_KEYS:
        value = os.getenv(key)
        if value:
            return value
    return None


def main() -> int:
    args = parse_args()
    pat = resolve_pat(args.pat)

    if not pat:
        sys.stderr.write(
            "ERROR: Provide a PAT via --pat argument or one of these env vars: "
            f"{', '.join(ENV_PAT_KEYS)}.\n"
        )
        return 1

    try:
        tree = fetch_page_tree(
            organization=args.organization,
            project=args.project,
            wiki=args.wiki,
            page_path=args.page_path,
            pat=pat,
        )
    except requests.HTTPError as exc:
        sys.stderr.write(f"HTTP error: {exc.response.status_code} {exc.response.text}\n")
        return 1
    except requests.RequestException as exc:
        sys.stderr.write(f"Request failed: {exc}\n")
        return 1

    output_root = Path(args.output_dir).resolve()
    paths = list(dict.fromkeys(collect_paths(tree)))
    if not paths:
        sys.stderr.write("WARNING: No wiki paths discovered.\n")
        return 1

    saved = 0
    skipped = []
    for wiki_path in paths:
        try:
            page = fetch_page_content(
                organization=args.organization,
                project=args.project,
                wiki=args.wiki,
                page_path=wiki_path,
                pat=pat,
            )
        except requests.HTTPError as exc:
            skipped.append((wiki_path, exc.response.status_code))
            continue
        except requests.RequestException as exc:
            skipped.append((wiki_path, str(exc)))
            continue

        content = page.get("content")
        if content is None:
            skipped.append((wiki_path, "no content"))
            continue
        file_path = page_to_file_path(output_root, wiki_path)
        save_page(content, file_path)
        saved += 1

    sys.stderr.write(f"Saved {saved} pages under {output_root}\n")
    if skipped:
        sys.stderr.write(
            "Skipped the following pages due to errors: "
            + ", ".join(f"{path} ({reason})" for path, reason in skipped)
            + "\n"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
