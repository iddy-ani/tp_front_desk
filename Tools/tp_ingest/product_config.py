"""Helpers for loading and matching product configuration documents."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, List, Optional

from . import models


def _strip_json_comments(raw_text: str) -> str:
    cleaned_lines = []
    for line in raw_text.splitlines():
        if "//" in line:
            prefix, *_ = line.split("//", 1)
            cleaned_lines.append(prefix.rstrip())
        else:
            cleaned_lines.append(line)
    return "\n".join(cleaned_lines)


def load_product_configs(path: Path) -> List[models.ProductConfig]:
    if not path.exists():
        raise FileNotFoundError(f"Product config file not found: {path}")

    text = path.read_text(encoding="utf-8")
    cleaned = _strip_json_comments(text)

    # Products.json is expected to be valid JSON. Some legacy config files historically
    # contained unescaped backslashes (e.g. Windows/UNC paths) which are not valid JSON.
    # To support both, only apply the backslash-doubling workaround if the initial parse fails.
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        data = json.loads(cleaned.replace("\\", "\\\\"))
    items: Iterable[dict]
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = [data]
    else:
        raise ValueError("Unexpected product config payload; expected JSON object or array")

    configs: List[models.ProductConfig] = []
    for item in items:
        configs.append(
            models.ProductConfig(
                product_code=item.get("ProductCode", ""),
                product_name=item.get("ProductName", ""),
                network_path=item.get("NetworkPath", ""),
                latest_tp=item.get("LatestTP"),
                number_of_releases=item.get("NumberOfReleases"),
                releases=item.get("ListOfReleases", []) or [],
                last_run_date=item.get("LastRunDate"),
                additional_attributes={k: v for k, v in item.items() if k not in {
                    "ProductCode",
                    "ProductName",
                    "NetworkPath",
                    "LatestTP",
                    "NumberOfReleases",
                    "ListOfReleases",
                    "LastRunDate",
                }},
            )
        )
    return configs


def find_product_config(
    configs: Iterable[models.ProductConfig],
    tp_name: str,
    product_code: Optional[str] = None,
) -> Optional[models.ProductConfig]:
    tp_name = tp_name.upper()
    if product_code:
        product_code = product_code.upper()
    best_match: Optional[models.ProductConfig] = None
    for config in configs:
        code = config.product_code.upper() if config.product_code else None
        if product_code and code != product_code:
            continue
        if (config.latest_tp or "").upper() == tp_name:
            return config
        if any(release.upper() == tp_name for release in config.releases):
            best_match = config
    if product_code and not best_match:
        for config in configs:
            code = config.product_code.upper() if config.product_code else None
            if code == product_code:
                return config
    return best_match
