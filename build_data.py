#!/usr/bin/env python3
"""
build_data.py
=============
Generates the public data/ directory from conference_cache/.

For each cached conference, copies the JSON file to data/ and builds
a data/index.json manifest listing all available conferences.

Run this after scraping new conferences:
    python build_data.py
"""

import json
import os
import shutil

CACHE_DIR = "conference_cache"
DATA_DIR = "data"

MONTH_LABEL = {4: "April", 10: "October"}


def main():
    os.makedirs(DATA_DIR, exist_ok=True)

    cache_files = sorted(f for f in os.listdir(CACHE_DIR) if f.endswith(".json"))
    if not cache_files:
        print("No cached conferences found. Run download_conference_talks.py first.")
        return

    index = []

    for filename in cache_files:
        conference_id = filename.replace(".json", "")
        src = os.path.join(CACHE_DIR, filename)
        dst = os.path.join(DATA_DIR, filename)

        with open(src) as fh:
            talks = json.load(fh)

        shutil.copy2(src, dst)

        year, month = conference_id.split("-")
        year, month = int(year), int(month)
        label = f"{MONTH_LABEL.get(month, month)} {year}"

        index.append({
            "id": conference_id,
            "label": label,
            "year": year,
            "month": month,
            "talkCount": len(talks),
        })

        print(f"  {conference_id}: {len(talks)} talks")

    index_path = os.path.join(DATA_DIR, "index.json")
    with open(index_path, "w", encoding="utf-8") as fh:
        json.dump(index, fh, ensure_ascii=False, indent=2)

    print(f"\nWrote {len(index)} conferences to {DATA_DIR}/")
    print(f"Wrote {index_path}")


if __name__ == "__main__":
    main()
