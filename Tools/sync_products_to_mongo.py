"""Sync Products.json to MongoDB product_configs collection.

This script reads Products.json and upserts all entries into the product_configs
collection so the Test Program Intelligence tool can list all available products.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def main() -> None:
    from pymongo import MongoClient

    MONGO_URI = (
        "mongodb://iq6cdegc265ebn7uiiem_admin:"
        "Yv0BqT17bakhZ9M%2CrL%3DbTPD0fqbVo%2Ch4@"
        "10-108-27-21.dbaas.intel.com:27017,"
        "10-108-27-23.dbaas.intel.com:27017,"
        "10-109-224-18.dbaas.intel.com:27017/"
        "tpfrontdesk?authSource=admin&tls=true"
    )

    products_path = Path(__file__).resolve().parent.parent / "Products.json"
    products = json.loads(products_path.read_text(encoding="utf-8"))

    print(f"Loaded {len(products)} products from {products_path.name}")

    client = MongoClient(MONGO_URI)
    product_configs = client["tpfrontdesk"]["product_configs"]

    for product in products:
        code = (product.get("ProductCode") or "").upper()
        if not code:
            continue

        update_doc = {
            "product_code": code,
            "product_name": product.get("ProductName"),
            "network_path": product.get("NetworkPath"),
            "latest_tp": product.get("LatestTP"),
            "number_of_releases": product.get("NumberOfReleases") or 0,
            "releases": product.get("ListOfReleases", []) or [],
            "last_run_date": product.get("LastRunDate"),
            "additional_attributes": {
                k: v
                for k, v in product.items()
                if k not in {
                    "ProductCode",
                    "ProductName",
                    "NetworkPath",
                    "LatestTP",
                    "NumberOfReleases",
                    "ListOfReleases",
                    "LastRunDate",
                }
            },
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        result = product_configs.update_one(
            {"product_code": code},
            {"$set": update_doc},
            upsert=True,
        )

        action = "inserted" if result.upserted_id else "updated"
        releases = update_doc["number_of_releases"]
        print(f"  {action} {code}: {update_doc['product_name']} ({releases} releases)")

    # Show final count
    count = product_configs.count_documents({})
    print(f"\nMongoDB product_configs now has {count} products")

    client.close()


if __name__ == "__main__":
    main()
