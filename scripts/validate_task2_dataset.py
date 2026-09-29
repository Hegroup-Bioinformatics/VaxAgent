#!/usr/bin/env python3
"""Validate Task 2 abstract input and optionally compare extraction output."""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path


EXPECTED_RESULT_COLUMNS = {
    "source_file",
    "skip_reason",
    "PMID",
    "Title",
    "Extracted Vaccine Name",
    "Proposed Vaccine Name",
    "Standardized Vaccine Name",
    "Combination Vaccine",
    "Vaccine Type",
    "Pathogen",
    "Gene or Protein Antigen",
    "Vaccine Formulation",
    "Adjuvant",
    "Target Vaccine Recipient or Host",
    "Lab Model or Cell",
    "Vaccine Development Stage",
    "Experiment Used to Investigate the Vaccine",
    "Vaccine Efficacy",
    "Immune Response",
    "Vaccine Introduction",
}


def read_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader), reader.fieldnames or []


def validate_input(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    rows, fields = read_csv(path)
    required = {"PMID", "Abstract"}
    missing = required - set(fields)
    if missing:
        raise ValueError(f"input is missing columns: {', '.join(sorted(missing))}")

    pmids = [row.get("PMID", "").strip() for row in rows]
    duplicate_pmids = sorted({pmid for pmid in pmids if pmid and pmids.count(pmid) > 1})
    missing_pmids = sum(not pmid for pmid in pmids)
    empty_abstracts = sum(not row.get("Abstract", "").strip() for row in rows)

    print(f"Input rows: {len(rows)}")
    print(f"Unique PMIDs: {len(set(pmid for pmid in pmids if pmid))}")
    print(f"Duplicate PMIDs: {len(duplicate_pmids)}")
    print(f"Missing PMIDs: {missing_pmids}")
    print(f"Empty abstracts: {empty_abstracts}")

    if duplicate_pmids:
        print("Duplicate PMID examples: " + ", ".join(duplicate_pmids[:10]))
    return rows, pmids


def compare_results(input_pmids: list[str], result_path: Path) -> None:
    results, fields = read_csv(result_path)
    missing_fields = EXPECTED_RESULT_COLUMNS - set(fields)
    if missing_fields:
        raise ValueError(
            f"result is missing columns: {', '.join(sorted(missing_fields))}"
        )

    input_set = {pmid for pmid in input_pmids if pmid}
    result_pmids = [row.get("PMID", "").strip() for row in results]
    result_set = {pmid for pmid in result_pmids if pmid}
    missing = sorted(input_set - result_set)
    unexpected = sorted(result_set - input_set)
    duplicate_results = sorted(
        {pmid for pmid in result_pmids if pmid and result_pmids.count(pmid) > 1}
    )
    malformed = sum(not row.get("PMID", "").strip() for row in results)

    print(f"Result rows: {len(results)}")
    print(f"Result unique PMIDs: {len(result_set)}")
    print(f"Missing input PMIDs in result: {len(missing)}")
    print(f"Unexpected result PMIDs: {len(unexpected)}")
    print(f"Duplicate result PMIDs: {len(duplicate_results)}")
    print(f"Result rows with missing PMID: {malformed}")

    if missing:
        print("Missing PMID examples: " + ", ".join(missing[:10]))
    if unexpected:
        print("Unexpected PMID examples: " + ", ".join(unexpected[:10]))
    if duplicate_results:
        print("Duplicate result examples: " + ", ".join(duplicate_results[:10]))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-csv", required=True, type=Path)
    parser.add_argument("--results-csv", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        _, input_pmids = validate_input(args.input_csv)
        if args.results_csv:
            compare_results(input_pmids, args.results_csv)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
