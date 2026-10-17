"""Add existing CSV leads to the configured database without overwriting saved records."""
import argparse
import csv
import sys

from configuration import configured_store
from storage import StorageError


def main():
    parser = argparse.ArgumentParser(description="Import an existing lead CSV; duplicates keep their saved data.")
    parser.add_argument("csv_file")
    args = parser.parse_args()
    with open(args.csv_file, encoding="utf-8-sig", newline="") as source:
        records = list(csv.DictReader(source))
    for row in records:
        row.setdefault("category", row.get("category_searched", ""))
        row.setdefault("city", row.get("city_searched", ""))
        for field in ("audit_score", "broken_links", "reviews"):
            if row.get(field):
                try:
                    row[field] = int(float(row[field]))
                except ValueError:
                    pass
        if row.get("rating"):
            try:
                row["rating"] = float(row["rating"])
            except ValueError:
                pass
    try:
        store = configured_store()
        before = len(store.load_leads())
        store.save_new_leads(records)
        print(f"Imported {len(store.load_leads()) - before} new leads. Existing records were preserved.")
    except StorageError as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
