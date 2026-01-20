"""Update Products.json and MongoDB product_configs with TP information from the scanner state file.

This script reads the daily_scanner_state.json and updates:
1. Products.json with:
   - LatestTP: The newest TP based on TP naming convention
   - NumberOfReleases: Count of ingested TPs
   - ListOfReleases: All TP names sorted newest to oldest
   - LastRunDate: Timestamp of the last scan

2. MongoDB product_configs collection with:
   - latest_tp, number_of_releases, releases fields
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

# MongoDB connection for updating product_configs
try:
    from pymongo import MongoClient
    MONGO_URI = (
        "mongodb://iq6cdegc265ebn7uiiem_admin:"
        "Yv0BqT17bakhZ9M%2CrL%3DbTPD0fqbVo%2Ch4@"
        "10-108-27-21.dbaas.intel.com:27017,"
        "10-108-27-23.dbaas.intel.com:27017,"
        "10-109-224-18.dbaas.intel.com:27017/"
        "tpfrontdesk?authSource=admin&tls=true"
    )
    MONGO_AVAILABLE = True
except ImportError:
    MONGO_AVAILABLE = False
    MongoClient = None  # type: ignore
    MONGO_URI = ""


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


def update_mongodb_product_configs(
    products_data: List[Dict[str, Any]],
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Update MongoDB product_configs collection with TP information.
    
    Args:
        products_data: List of product dicts with LatestTP, NumberOfReleases, ListOfReleases
        dry_run: If True, only show what would be updated
    
    Returns:
        Summary of MongoDB updates
    """
    if not MONGO_AVAILABLE:
        logger.warning("MongoDB not available (pymongo not installed)")
        return {"error": "pymongo not installed", "updated": 0}
    
    try:
        client = MongoClient(MONGO_URI)
        product_configs = client["tpfrontdesk"]["product_configs"]
    except Exception as e:
        logger.error(f"MongoDB connection failed: {e}")
        return {"error": str(e), "updated": 0}
    
    updated_count = 0
    for product in products_data:
        code = product.get("ProductCode", "")
        latest_tp = product.get("LatestTP")
        num_releases = product.get("NumberOfReleases", 0)
        releases = product.get("ListOfReleases", [])
        
        if not code or not releases:
            continue
        
        if dry_run:
            logger.info(f"  [MongoDB DRY RUN] Would update {code}: {num_releases} releases")
        else:
            result = product_configs.update_one(
                {"product_code": code},
                {"$set": {
                    "latest_tp": latest_tp,
                    "number_of_releases": num_releases,
                    "releases": releases,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }}
            )
            if result.modified_count > 0:
                updated_count += 1
                logger.info(f"  [MongoDB] Updated {code}: {num_releases} releases")
    
    return {"updated": updated_count, "dry_run": dry_run}


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
        
        # Also update MongoDB
        logger.info("\nUpdating MongoDB product_configs...")
        mongo_result = update_mongodb_product_configs(products, dry_run=False)
        changes["mongodb"] = mongo_result
    else:
        logger.info("\n[DRY RUN] Products.json not modified")
        # Show what MongoDB would get
        mongo_result = update_mongodb_product_configs(products, dry_run=True)
        changes["mongodb"] = mongo_result
    
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
