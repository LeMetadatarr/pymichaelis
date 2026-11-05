"""Build the final TSV from ``entries.jsonl``."""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Iterable, Iterator, Optional

from pymichaelis.client import DICTIONARIES
from pymichaelis.crawl import first_form
from pymichaelis.models import FOUND
from pymichaelis.notation import UnknownSymbol, to_ipa

COLUMNS = ("word", "homograph", "ipa", "raw", "syllables", "pos", "dictionary", "url")


def read_rows(path: Path) -> Iterator[dict]:
    with path.open(encoding="utf-8") as log:
        for line in log:
            try:
                yield json.loads(line)
            except ValueError:
                continue


def unique_entries(rows: Iterable[dict]) -> list[dict]:
    """Keep one row per headword and homograph.

    Dictionaries rank in :data:`DICTIONARIES` order, so a word in the Spanish
    dictionary keeps its Spanish row.
    """
    rank = {name: position for position, name in enumerate(DICTIONARIES)}
    found = [row for row in rows if row["status"] == FOUND]
    best: dict[tuple[str, Optional[int]], dict] = {}
    for row in sorted(found, key=lambda row: rank[row["dictionary"]]):
        best.setdefault((first_form(row["headword"]), row["homograph"]), row)
    return sorted(best.values(), key=lambda row: (row["headword"], row["homograph"] or 0))


def _ipa(raw: str) -> str:
    try:
        return to_ipa(raw)
    except UnknownSymbol:
        return ""


def write_tsv(entries: Iterable[dict], path: Path) -> int:
    """Write ``entries``. The IPA comes fresh from ``raw``. Return the row count."""
    written = 0
    with path.open("w", encoding="utf-8", newline="") as sink:
        writer = csv.writer(sink, delimiter="\t", lineterminator="\n")
        writer.writerow(COLUMNS)
        for row in entries:
            writer.writerow(
                (
                    first_form(row["headword"]),
                    row["homograph"] or "",
                    _ipa(row["raw"]),
                    row["raw"],
                    row["syllables"] or "",
                    "; ".join(row["pos"]),
                    row["dictionary"],
                    row["url"],
                )
            )
            written += 1
    return written


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("entries", type=Path)
    parser.add_argument("tsv", type=Path)
    args = parser.parse_args(argv)
    rows = list(read_rows(args.entries))
    entries = unique_entries(rows)
    written = write_tsv(entries, args.tsv)
    print(f"{len(rows)} rows read, {len(entries)} unique entries, {written} written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
