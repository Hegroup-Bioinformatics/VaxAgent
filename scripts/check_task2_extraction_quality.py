#!/usr/bin/env python3
"""Flag quality issues in Task 2 LLM extraction results without rewriting them."""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path


REQUIRED_FIELDS = [
    "PMID",
    "Title",
    "Extracted Vaccine Name",
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
]

SPECIES_RE = re.compile(r"\bBrucella\s+(abortus|melitensis|suis|ovis|canis)\b", re.I)


def check_row(row: dict[str, str]) -> list[str]:
    issues: list[str] = []
    pmid = row.get("PMID", "").strip()
    source_file = row.get("source_file", "").strip()
    if not pmid:
        match = re.fullmatch(r"(\d+)\.txt", source_file)
        if match:
            pmid = match.group(1)
        else:
            issues.append("missing_pmid")

    if row.get("skip_reason", "").strip():
        return issues

    standardized = row.get("Standardized Vaccine Name", "").strip()
    pathogen = row.get("Pathogen", "").strip()
    if standardized and not standardized.isascii():
        issues.append("non_ascii_standardized_name")

    pathogen_match = SPECIES_RE.search(pathogen)
    standardized_match = SPECIES_RE.search(standardized)
    if pathogen_match and standardized_match:
        if pathogen_match.group(1).lower() != standardized_match.group(1).lower():
            issues.append("pathogen_standardized_name_mismatch")

    for field in REQUIRED_FIELDS:
        if not row.get(field, "").strip():
            issues.append(f"missing_field:{field}")

    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-csv", required=True, type=Path)
    parser.add_argument("--flagged-csv", type=Path)
    args = parser.parse_args()

    with args.input_csv.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        fields = reader.fieldnames or []

    flagged: list[dict[str, str]] = []
    counts: dict[str, int] = {}
    for row in rows:
        issues = check_row(row)
        if issues:
            flagged_row = dict(row)
            flagged_row["quality_issues"] = ";".join(issues)
            flagged.append(flagged_row)
            for issue in issues:
                counts[issue] = counts.get(issue, 0) + 1

    print(f"Input result rows: {len(rows)}")
    print(f"Flagged rows: {len(flagged)}")
    for issue, count in sorted(counts.items()):
        print(f"{issue}: {count}")

    if args.flagged_csv:
        args.flagged_csv.parent.mkdir(parents=True, exist_ok=True)
        output_fields = fields + ["quality_issues"]
        with args.flagged_csv.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=output_fields)
            writer.writeheader()
            writer.writerows(flagged)
        print(f"Wrote flagged rows: {args.flagged_csv.resolve()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
