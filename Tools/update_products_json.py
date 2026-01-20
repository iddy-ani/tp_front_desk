"""Update Products.json with TP information from the scanner state file.

This script reads the daily_scanner_state.json and updates Products.json with:
- LatestTP: The newest TP based on TP naming convention
- NumberOfReleases: Count of ingested TPs
- ListOfReleases: All TP names sorted newest to oldest
- LastRunDate: Timestamp of the last scan
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def tp_name_sort_key(tp_name: str) -> Tuple[str, int, str, int, str]:
    """Extract a sort key from a TP name based on Intel naming standard.
    
    TP naming convention (18 chars):
    - [10-12]: TP Revision - primary sort key
    - [13]: Milestone (0-4)
    - [14]: Sequential Release Number (0-9)
    - [15-18]: Release Date (YYWW)
    """
    if not tp_name or len(tp_name) < 15:
        return ("000", 0, "0", 0, "0000")
    
    tp_upper = tp_name.upper()
    tp_revision = tp_upper[10:13] if len(tp_upper) >= 13 else "000"
    milestone = int(tp_upper[13]) if len(tp_upper) >= 14 and tp_upper[13].isdigit() else 0
    release_num = tp_upper[14] if len(tp_upper) >= 15 else "0"
    release_date = tp_upper[15:19] if len(tp_upper) >= 19 else "0000"
    
    return (tp_revision, milestone, release_num, 0, release_date)


def update_products_json(
    products_path: Path,
    state_path: Path,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Update Products.json with TP information from scanner state.
    
    Returns a summary of changes made.
    """
    if not state_path.exists():
        raise FileNotFoundError(f"State file not found: {state_path}")
    if not products_path.exists():
        raise FileNotFoundError(f"Products file not found: {products_path}")
    
    state = json.loads(state_path.read_text(encoding="utf-8"))
    products = json.loads(products_path.read_text(encoding="utf-8"))
    
    changes: Dict[str, Any] = {"updated_products": [], "summary": {}}
    
    for product in products:
        code = (product.get("ProductCode") or "").upper()
        product_state = state.get("products", {}).get(code, {})
        processed = product_state.get("processed_tps", {})
        
        if not processed:
            logger.info(f"  {code}: No TPs ingested yet")
            continue
        
        # Get all TP names and sort by naming convention (newest first)
        tp_names = list(processed.keys())
        tp_names.sort(key=tp_name_sort_key, reverse=True)
        
        latest_tp = tp_names[0] if tp_names else None
        last_scan = product_state.get("last_scan")
        
        # Track changes
        old_latest = product.get("LatestTP")
        old_count = product.get("NumberOfReleases") or 0
        
        # Update product
        product["LatestTP"] = latest_tp
        product["NumberOfReleases"] = len(tp_names)
        product["ListOfReleases"] = tp_names
        product["LastRunDate"] = last_scan
        
        changes["updated_products"].append({
            "product_code": code,
            "product_name": product.get("ProductName"),
            "tp_count": len(tp_names),
            "latest_tp": latest_tp,
            "previous_latest": old_latest,
            "added_count": len(tp_names) - old_count,
        })
        
        logger.info(f"  {code}: {len(tp_names)} TPs, Latest: {latest_tp}")
    
    if not dry_run:
        products_path.write_text(json.dumps(products, indent=4), encoding="utf-8")
        logger.info(f"\nProducts.json updated at {products_path}")
    else:
        logger.info("\n[DRY RUN] Products.json not modified")
    
    changes["summary"] = {
        "total_products": len(products),
        "products_with_tps": len(changes["updated_products"]),
        "dry_run": dry_run,
    }
    
    return changes


def main() -> None:
    import argparse
    
    parser = argparse.ArgumentParser(description="Update Products.json from scanner state")
    parser.add_argument(
        "--products-file",
        type=Path,
        default=Path(__file__).parent.parent / "Products.json",
        help="Path to Products.json",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=Path(__file__).parent.parent / "state" / "daily_scanner_state.json",
        help="Path to daily_scanner_state.json",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be updated without making changes",
    )
    
    args = parser.parse_args()
    
    logger.info("Updating Products.json from scanner state...\n")
    changes = update_products_json(
        products_path=args.products_file.resolve(),
        state_path=args.state_file.resolve(),
        dry_run=args.dry_run,
    )
    
    print(json.dumps(changes, indent=2))


if __name__ == "__main__":
    main()
