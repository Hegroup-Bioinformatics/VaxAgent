#!/usr/bin/env python3
"""Review extracted vaccine candidates against existing VIOLIN vaccine table."""

from __future__ import annotations

import argparse
import csv
import difflib
import re
from collections import defaultdict
from pathlib import Path


def normalize_name(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^a-z0-9]+", "", text)
    return text


def load_existing_vaccines(vaccine_csv: Path) -> tuple[dict[str, list[dict[str, str]]], list[dict[str, str]]]:
    by_norm: dict[str, list[dict[str, str]]] = defaultdict(list)
    all_rows: list[dict[str, str]] = []

    with vaccine_csv.open("r", encoding="utf-8", errors="ignore", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            all_rows.append(row)
            names = [
                row.get("c_vaccine_name", "").strip(),
                row.get("c_proper_name", "").strip(),
                row.get("c_brand_name", "").strip(),
            ]
            for name in names:
                if not name:
                    continue
                norm = normalize_name(name)
                if norm:
                    by_norm[norm].append(row)

    return by_norm, all_rows


def load_extracted_candidates(extracted_csv: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with extracted_csv.open("r", encoding="utf-8", errors="ignore", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            rows.append(row)
    return rows


def split_candidates(raw: str) -> list[str]:
    if not raw.strip():
        return []
    return [part.strip() for part in raw.split("|") if part.strip()]


def match_candidate(
    candidate: str,
    existing_by_norm: dict[str, list[dict[str, str]]],
    all_existing: list[dict[str, str]],
) -> tuple[str, str, str, str]:
    cand_norm = normalize_name(candidate)
    if not cand_norm:
        return ("new", "", "", "empty-normalized")

    # Exact normalized match.
    exact = existing_by_norm.get(cand_norm)
    if exact:
        first = exact[0]
        return (
            "existing",
            first.get("c_vaccine_id", ""),
            first.get("c_vaccine_name", ""),
            "exact-normalized",
        )

    # Contains match for longer informative strings.
    if len(cand_norm) >= 6:
        for row in all_existing:
            existing_name = row.get("c_vaccine_name", "")
            existing_norm = normalize_name(existing_name)
            if not existing_norm:
                continue
            if cand_norm in existing_norm or existing_norm in cand_norm:
                return (
                    "possible",
                    row.get("c_vaccine_id", ""),
                    row.get("c_vaccine_name", ""),
                    "contains",
                )

    # Fuzzy fallback.
    existing_norms = list(existing_by_norm.keys())
    if existing_norms:
        closest = difflib.get_close_matches(cand_norm, existing_norms, n=1, cutoff=0.9)
        if closest:
            row = existing_by_norm[closest[0]][0]
            return (
                "possible",
                row.get("c_vaccine_id", ""),
                row.get("c_vaccine_name", ""),
                "fuzzy-0.9",
            )

    return ("new", "", "", "no-match")


def build_candidate_rows(
    extracted_rows: list[dict[str, str]],
    existing_by_norm: dict[str, list[dict[str, str]]],
    all_existing: list[dict[str, str]],
) -> list[dict[str, str]]:
    grouped: dict[str, dict[str, str]] = {}

    for row in extracted_rows:
        pmid = row.get("pmid", "")
        title = row.get("title", "")
        candidates = split_candidates(row.get("candidate_vaccine_names", ""))

        for candidate in candidates:
            key = candidate.lower()
            if key not in grouped:
                grouped[key] = {
                    "candidate_name": candidate,
                    "occurrence_count": "0",
                    "example_pmids": "",
                    "example_titles": "",
                }

            item = grouped[key]
            item["occurrence_count"] = str(int(item["occurrence_count"]) + 1)

            pmids = [p for p in item["example_pmids"].split(" | ") if p]
            if pmid and pmid not in pmids and len(pmids) < 5:
                pmids.append(pmid)
            item["example_pmids"] = " | ".join(pmids)

            titles = [t for t in item["example_titles"].split(" | ") if t]
            if title and title not in titles and len(titles) < 3:
                titles.append(title)
            item["example_titles"] = " | ".join(titles)

    result_rows: list[dict[str, str]] = []
    for item in grouped.values():
        status, match_id, match_name, method = match_candidate(
            item["candidate_name"], existing_by_norm, all_existing
        )
        item["match_status"] = status
        item["matched_vaccine_id"] = match_id
        item["matched_vaccine_name"] = match_name
        item["match_method"] = method
        result_rows.append(item)

    result_rows.sort(key=lambda r: (r["match_status"], -int(r["occurrence_count"]), r["candidate_name"].lower()))
    return result_rows


def write_csv(rows: list[dict[str, str]], path: Path, fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_review_markdown(
    path: Path,
    extracted_count: int,
    candidate_rows: list[dict[str, str]],
    output_matches: Path,
    output_new: Path,
) -> None:
    existing = [r for r in candidate_rows if r["match_status"] == "existing"]
    possible = [r for r in candidate_rows if r["match_status"] == "possible"]
    new = [r for r in candidate_rows if r["match_status"] == "new"]

    lines = [
        "# Extraction Review",
        "",
        f"- Abstract records processed: {extracted_count}",
        f"- Unique candidate vaccine names: {len(candidate_rows)}",
        f"- Existing matches: {len(existing)}",
        f"- Possible matches (manual review): {len(possible)}",
        f"- New candidates: {len(new)}",
        "",
        "## Output Files",
        f"- Candidate matches: {output_matches}",
        f"- New vaccine candidates: {output_new}",
        "",
        "## Manual Curation Priorities",
        "- Review all `possible` matches where names are similar but not exact.",
        "- Check short candidate strings (for example strain aliases) for context-specific meaning.",
        "- Confirm that each `new` candidate represents a vaccine entity, not a generic platform phrase.",
        "",
        "## Top New Candidates",
    ]

    for row in sorted(new, key=lambda r: int(r["occurrence_count"]), reverse=True)[:20]:
        lines.append(
            f"- {row['candidate_name']} (count={row['occurrence_count']}, pmids={row['example_pmids']})"
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Review extracted vaccine candidates against VIOLIN vaccine table."
    )
    parser.add_argument(
        "--extracted-csv",
        default="outputs/extracted_vaccine_records.csv",
        help="CSV generated by extract_vaccine_data_from_abstracts.py",
    )
    parser.add_argument(
        "--violin-vaccine-csv",
        default="violin-data/t_vaccine.csv",
        help="VIOLIN vaccine table CSV",
    )
    parser.add_argument(
        "--out-matches",
        default="outputs/candidate_vaccine_matches.csv",
        help="Output CSV containing all candidate matches",
    )
    parser.add_argument(
        "--out-new",
        default="outputs/new_vaccine_candidates.csv",
        help="Output CSV containing only new candidates",
    )
    parser.add_argument(
        "--out-review-md",
        default="outputs/extraction_review.md",
        help="Output markdown review summary",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    extracted_csv = Path(args.extracted_csv)
    vaccine_csv = Path(args.violin_vaccine_csv)
    out_matches = Path(args.out_matches)
    out_new = Path(args.out_new)
    out_review = Path(args.out_review_md)

    existing_by_norm, all_existing = load_existing_vaccines(vaccine_csv)
    extracted_rows = load_extracted_candidates(extracted_csv)
    candidate_rows = build_candidate_rows(extracted_rows, existing_by_norm, all_existing)

    fields = [
        "candidate_name",
        "occurrence_count",
        "example_pmids",
        "example_titles",
        "match_status",
        "matched_vaccine_id",
        "matched_vaccine_name",
        "match_method",
    ]

    write_csv(candidate_rows, out_matches, fields)
    new_only = [row for row in candidate_rows if row["match_status"] == "new"]
    write_csv(new_only, out_new, fields)
    write_review_markdown(out_review, len(extracted_rows), candidate_rows, out_matches, out_new)

    print(f"Extracted rows reviewed: {len(extracted_rows)}")
    print(f"Unique candidate names: {len(candidate_rows)}")
    print(f"New candidates: {len(new_only)}")
    print(f"Wrote: {out_matches.resolve()}")
    print(f"Wrote: {out_new.resolve()}")
    print(f"Wrote: {out_review.resolve()}")


if __name__ == "__main__":
    main()
