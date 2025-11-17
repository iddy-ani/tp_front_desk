"""Utility to download Azure DevOps wiki pages.

This script authenticates with a Personal Access Token (PAT), calls the Azure DevOps
Wiki REST API, and writes the requested page content to stdout and an optional file.
"""

import argparse
import base64
import os
import sys
from pathlib import Path
from typing import Optional

import requests

API_VERSION = "7.0"
DEFAULT_ORG = "mit-is"
DEFAULT_PROJECT = "TorchWiki"
DEFAULT_WIKI = "TorchWiki.wiki"
DEFAULT_PAGE_PATH = "OTPL-language-and-files"
DEFAULT_OUTPUT = "OTPL-language-and-files.md"
ENV_PAT_KEYS = ("TORCH_WIKI_READ_TOKEN", "AZDO_PAT")


def build_auth_header(pat: str) -> str:
    token = base64.b64encode(f":{pat}".encode("ascii")).decode("ascii")
    return f"Basic {token}"


def fetch_wiki_page(*, organization: str, project: str, wiki: str, page_path: str, pat: str) -> dict:
    url = f"https://dev.azure.com/{organization}/{project}/_apis/wiki/wikis/{wiki}/pages"
    params = {
        "path": page_path,
        "includeContent": "true",
        "api-version": API_VERSION,
    }

    headers = {"Authorization": build_auth_header(pat)}
    response = requests.get(url, params=params, headers=headers, timeout=30)
    response.raise_for_status()
    return response.json()


def write_content(content: str, output_path: Optional[Path]) -> None:
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")
    sys.stdout.write(content)
    if not content.endswith("\n"):
        sys.stdout.write("\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download Azure DevOps wiki page content.")
    parser.add_argument("--organization", default=DEFAULT_ORG, help="Azure DevOps organization name")
    parser.add_argument("--project", default=DEFAULT_PROJECT, help="Azure DevOps project name")
    parser.add_argument("--wiki", default=DEFAULT_WIKI, help="Wiki identifier (e.g., TorchWiki.wiki)")
    parser.add_argument("--page-path", default=DEFAULT_PAGE_PATH, help="Wiki page path (e.g., Torch)")
    parser.add_argument("--pat", help="Azure DevOps PAT. Defaults to AZDO_PAT environment variable.")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Optional path to save content")
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
        payload = fetch_wiki_page(
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

    content = payload.get("content")
    if content is None:
        sys.stderr.write("WARNING: No content returned. Check the page path or permissions.\n")
        return 1

    output_path = Path(args.output).resolve() if args.output else None
    write_content(content, output_path)
    sys.stderr.write(f"Saved page content to {output_path}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
