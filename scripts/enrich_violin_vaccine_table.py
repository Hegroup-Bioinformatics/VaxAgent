#!/usr/bin/env python3
"""Enrich VIOLIN t_vaccine with pathogen names, host names, and PMIDs."""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path


REF_ID_RE = re.compile(r"\[\s*Ref\s*(\d+)\s*:", re.IGNORECASE)
ID_SPLIT_RE = re.compile(r"[\s,;|/]+")


def parse_id_list(raw: str) -> list[str]:
    text = (raw or "").strip()
    if not text:
        return []
    parts = [p.strip() for p in ID_SPLIT_RE.split(text) if p.strip()]
    out: list[str] = []
    for part in parts:
        if part.isdigit():
            out.append(part)
    return out


def unique_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", errors="ignore", newline="") as handle:
        return list(csv.DictReader(handle))


def build_lookup(rows: list[dict[str, str]], id_key: str, value_key: str) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for row in rows:
        row_id = (row.get(id_key) or "").strip()
        value = (row.get(value_key) or "").strip()
        if row_id:
            lookup[row_id] = value
    return lookup


def build_reference_lookup(rows: list[dict[str, str]]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for row in rows:
        ref_id = (row.get("c_reference_id") or "").strip()
        pmid = (row.get("c_pmid") or "").strip()
        if ref_id:
            lookup[ref_id] = pmid
    return lookup


def extract_reference_ids_from_description(description: str) -> list[str]:
    hits = REF_ID_RE.findall(description or "")
    return unique_keep_order(hits)


def resolve_names(ids: list[str], lookup: dict[str, str]) -> list[str]:
    names: list[str] = []
    for item_id in ids:
        name = lookup.get(item_id, "")
        if name:
            names.append(name)
    return unique_keep_order(names)


def resolve_pmids(ref_ids: list[str], ref_lookup: dict[str, str]) -> list[str]:
    pmids: list[str] = []
    for ref_id in ref_ids:
        raw = ref_lookup.get(ref_id, "")
        if not raw:
            continue
        cleaned = raw.strip()
        if cleaned:
            pmids.append(cleaned)
    return unique_keep_order(pmids)


def enrich_rows(
    vaccine_rows: list[dict[str, str]],
    pathogen_lookup: dict[str, str],
    host_lookup: dict[str, str],
    reference_lookup: dict[str, str],
) -> list[dict[str, str]]:
    enriched: list[dict[str, str]] = []

    for row in vaccine_rows:
        out = dict(row)

        pathogen_ids = parse_id_list(row.get("c_pathogen_id", ""))
        host_ids_1 = parse_id_list(row.get("c_host_species", ""))
        host_ids_2 = parse_id_list(row.get("c_host_species2", ""))

        description = row.get("c_description", "")
        ref_ids = extract_reference_ids_from_description(description)

        pathogen_names = resolve_names(pathogen_ids, pathogen_lookup)
        host_names_1 = resolve_names(host_ids_1, host_lookup)
        host_names_2 = resolve_names(host_ids_2, host_lookup)
        pmids = resolve_pmids(ref_ids, reference_lookup)

        out["resolved_pathogen_name"] = " | ".join(pathogen_names)
        out["resolved_host_species_name"] = " | ".join(host_names_1)
        out["resolved_host_species2_name"] = " | ".join(host_names_2)
        out["resolved_reference_ids_from_description"] = " | ".join(ref_ids)
        out["resolved_pmids_from_description"] = " | ".join(pmids)

        enriched.append(out)

    return enriched


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    if not rows:
        raise ValueError("No rows to write.")

    base_fields = list(rows[0].keys())
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=base_fields)
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Enrich VIOLIN t_vaccine with host/pathogen names and PMIDs from references."
    )
    parser.add_argument(
        "--vaccine-csv",
        default="violin-data/t_vaccine.csv",
        help="Path to t_vaccine.csv",
    )
    parser.add_argument(
        "--pathogen-csv",
        default="violin-data/t_pathogen.csv",
        help="Path to t_pathogen.csv",
    )
    parser.add_argument(
        "--host-csv",
        default="violin-data/t_host.csv",
        help="Path to t_host.csv",
    )
    parser.add_argument(
        "--reference-csv",
        default="violin-data/t_reference.csv",
        help="Path to t_reference.csv",
    )
    parser.add_argument(
        "--out-csv",
        default="inputs/t_vaccine_enriched.csv",
        help="Output enriched CSV path",
    )
    return parser.parse_args()


def main() -> None:
    csv.field_size_limit(sys.maxsize)
    args = parse_args()

    vaccine_rows = load_csv(Path(args.vaccine_csv))
    pathogen_rows = load_csv(Path(args.pathogen_csv))
    host_rows = load_csv(Path(args.host_csv))
    reference_rows = load_csv(Path(args.reference_csv))

    pathogen_lookup = build_lookup(pathogen_rows, "c_pathogen_id", "c_pathogen_name")
    host_lookup = build_lookup(host_rows, "c_host_id", "c_host_name")
    reference_lookup = build_reference_lookup(reference_rows)

    enriched = enrich_rows(vaccine_rows, pathogen_lookup, host_lookup, reference_lookup)
    out_path = Path(args.out_csv)
    write_csv(out_path, enriched)

    with_pmids = sum(1 for row in enriched if row.get("resolved_pmids_from_description", ""))
    with_pathogen = sum(1 for row in enriched if row.get("resolved_pathogen_name", ""))
    with_host = sum(1 for row in enriched if row.get("resolved_host_species_name", "") or row.get("resolved_host_species2_name", ""))

    print(f"Input vaccines: {len(vaccine_rows)}")
    print(f"Rows with pathogen names: {with_pathogen}")
    print(f"Rows with host names: {with_host}")
    print(f"Rows with PMIDs from description refs: {with_pmids}")
    print(f"Wrote: {out_path.resolve()}")


if __name__ == "__main__":
    main()