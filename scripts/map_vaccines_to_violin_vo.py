#!/usr/bin/env python3
"""Map extracted vaccine records to VIOLIN and VO (ROBOT template)."""

from __future__ import annotations

import argparse
import csv
import html
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from openpyxl import Workbook


def clean_text(value: str) -> str:
    return html.unescape((value or "").strip())


def normalize_text(value: str) -> str:
    text = clean_text(value).lower()
    text = re.sub(r"\s+", " ", text)
    return text


def normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", normalize_text(value))


def split_multi(value: str) -> list[str]:
    text = clean_text(value)
    if not text or normalize_text(text) in {"not reported", "none", "not applicable"}:
        return []
    parts = re.split(r"\s*\|\s*|\s*;\s*", text)
    return [p.strip() for p in parts if p.strip()]


def extract_pmids(value: str) -> set[str]:
    return set(re.findall(r"\b\d{6,9}\b", value or ""))


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", errors="ignore", newline="") as handle:
        reader = csv.DictReader(handle)
        rows: list[dict[str, str]] = []
        for row in reader:
            clean_row: dict[str, str] = {}
            for key, value in row.items():
                clean_row[key.lstrip("\ufeff")] = value or ""
            rows.append(clean_row)
        return rows


@dataclass
class VoEntry:
    vo_id: str
    label: str
    pmids: set[str]
    is_brucella: bool


def load_vo_template(path: Path) -> tuple[list[VoEntry], dict[str, list[VoEntry]]]:
    with path.open("r", encoding="utf-8", errors="ignore", newline="") as handle:
        reader = csv.reader(handle)
        all_rows = list(reader)

    if len(all_rows) < 3:
        return [], {}

    header = [clean_text(h).lstrip("\ufeff") for h in all_rows[0]]
    template = all_rows[1]
    keep_idx = [i for i, v in enumerate(template) if clean_text(v)]
    keep_headers = [header[i] for i in keep_idx]

    out: list[VoEntry] = []
    by_pmid: dict[str, list[VoEntry]] = defaultdict(list)

    for raw in all_rows[2:]:
        row = {keep_headers[i]: (raw[keep_idx[i]] if keep_idx[i] < len(raw) else "") for i in range(len(keep_headers))}
        vo_id = clean_text(row.get("ID", ""))
        label = clean_text(row.get("LABEL", ""))
        if not vo_id and not label:
            continue

        source = clean_text(row.get("definition source", ""))
        pmids = extract_pmids(source)

        brucella_probe = " | ".join(
            [
                label,
                clean_text(row.get("immunizes against pathogen", "")),
                clean_text(row.get("violin_pathogen", "")),
            ]
        )
        is_brucella = "brucella" in normalize_text(brucella_probe)

        entry = VoEntry(vo_id=vo_id, label=label, pmids=pmids, is_brucella=is_brucella)
        out.append(entry)
        for pmid in pmids:
            by_pmid[pmid].append(entry)

    return out, by_pmid


def build_violin_indexes(rows: list[dict[str, str]]) -> tuple[dict[str, list[dict[str, str]]], dict[str, list[dict[str, str]]]]:
    by_name: dict[str, list[dict[str, str]]] = defaultdict(list)
    by_pmid: dict[str, list[dict[str, str]]] = defaultdict(list)

    for row in rows:
        for name in [row.get("c_vaccine_name", ""), row.get("c_proper_name", ""), row.get("c_brand_name", "")]:
            key = normalize_key(name)
            if key:
                by_name[key].append(row)
        for pmid in extract_pmids(row.get("resolved_pmids_from_description", "")):
            by_pmid[pmid].append(row)

    return by_name, by_pmid


def candidate_names(record: dict[str, str]) -> list[str]:
    values = [
        clean_text(record.get("Standardized Vaccine Name", "")),
        clean_text(record.get("Proposed Vaccine Name", "")),
        clean_text(record.get("Make Vaccine Name", "")),
        clean_text(record.get("Extracted Vaccine Name", "")),
    ]
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value or normalize_text(value) == "not reported":
            continue
        key = normalize_key(value)
        if key and key not in seen:
            seen.add(key)
            out.append(value)
    return out


def is_brucella_record(record: dict[str, str]) -> bool:
    pathogen = normalize_text(record.get("Pathogen", ""))
    return bool(pathogen and pathogen not in {"not reported", "none"} and "brucella" in pathogen)


def name_similarity_level(left: str, right: str) -> int:
    lkey = normalize_key(left)
    rkey = normalize_key(right)
    if not lkey or not rkey:
        return 0
    if lkey == rkey:
        return 3
    if lkey in rkey or rkey in lkey:
        return 2
    return 0


def map_violin(record: dict[str, str], by_name: dict[str, list[dict[str, str]]], by_pmid: dict[str, list[dict[str, str]]]) -> tuple[str, str, str, str, str]:
    if not is_brucella_record(record):
        return "", "", "not mapped", "low", "non-Brucella pathogen in extraction record"

    pmid = clean_text(record.get("PMID", ""))
    names = candidate_names(record)

    # PMID is primary mapping signal.
    pmid_candidates = by_pmid.get(pmid, []) if pmid else []
    best: tuple[int, dict[str, str], str] | None = None
    for candidate in pmid_candidates:
        c_names = [candidate.get("c_vaccine_name", ""), candidate.get("c_proper_name", ""), candidate.get("c_brand_name", "")]
        local_best = 0
        local_name = ""
        for qname in names:
            for cname in c_names:
                score = name_similarity_level(qname, cname)
                if score > local_best:
                    local_best = score
                    local_name = cname
        if best is None or local_best > best[0]:
            best = (local_best, candidate, local_name)

    if best is not None:
        score, row, matched_name = best
        if score >= 3:
            return row.get("c_vaccine_id", ""), row.get("c_vaccine_name", ""), "exact", "high", "PMID matched; vaccine name exact"
        if score >= 2:
            return row.get("c_vaccine_id", ""), row.get("c_vaccine_name", ""), "possible", "high", "PMID matched; vaccine name partially aligned"
        return row.get("c_vaccine_id", ""), row.get("c_vaccine_name", ""), "possible", "medium", "PMID matched; weak vaccine-name evidence"

    # Fallback: name-only lookup when PMID is unavailable or unmatched.
    for qname in names:
        exact_rows = by_name.get(normalize_key(qname), [])
        if exact_rows:
            row = exact_rows[0]
            return row.get("c_vaccine_id", ""), row.get("c_vaccine_name", ""), "possible", "medium", "name-only match without PMID support"

    return "", "", "not mapped", "low", "no credible VIOLIN match"


def map_vo(record: dict[str, str], vo_by_pmid: dict[str, list[VoEntry]]) -> tuple[str, str, str, str]:
    if not is_brucella_record(record):
        return "", "", "not mapped", "no matched VO terms"

    pmid = clean_text(record.get("PMID", ""))
    if not pmid:
        return "", "", "not mapped", "missing PMID for VO mapping"

    candidates = [entry for entry in vo_by_pmid.get(pmid, []) if entry.is_brucella]
    if not candidates:
        return "", "", "not mapped", "no Brucella VO entry matched by PMID"

    names = candidate_names(record)
    if not names:
        chosen = candidates[0]
        return chosen.vo_id, chosen.label, "possible", "PMID matched; no usable vaccine name"

    best: tuple[int, VoEntry] | None = None
    for entry in candidates:
        local_best = 0
        for name in names:
            local_best = max(local_best, name_similarity_level(name, entry.label))
        if best is None or local_best > best[0]:
            best = (local_best, entry)

    assert best is not None
    score, chosen = best
    if score >= 3:
        return chosen.vo_id, chosen.label, "exact", "PMID matched and vaccine name exact"
    if score >= 2:
        return chosen.vo_id, chosen.label, "possible", "PMID matched and vaccine name partially aligned"
    return chosen.vo_id, chosen.label, "possible", "PMID matched; weak vaccine-name evidence"


def combined_confidence(violin_conf: str, vo_status: str) -> str:
    if violin_conf == "high":
        return "high"
    if violin_conf == "medium" or vo_status == "possible":
        return "medium"
    return violin_conf or "low"


def write_workbook(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "vaccine_mapping"
    ws.append(fieldnames)
    for row in rows:
        ws.append([row.get(field, "") for field in fieldnames])
    ws.freeze_panes = "A2"
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Map extracted vaccine records to VIOLIN and VO template.")
    parser.add_argument("--input-csv", default="vaccine-data/llm_vaccine_extraction_results.csv")
    parser.add_argument("--violin-csv", default="inputs/t_vaccine_enriched.csv")
    parser.add_argument("--vo-template-csv", default="inputs/vaccine.csv")
    parser.add_argument("--limit", type=int, default=0, help="Only process first N rows (0 = all).")
    parser.add_argument("--out-xlsx", default="outputs/brucella_vaccine_mapping.xlsx")
    parser.add_argument("--out-csv", default="outputs/brucella_vaccine_mapping.csv")
    return parser.parse_args()


def main() -> None:
    csv.field_size_limit(sys.maxsize)
    args = parse_args()

    input_rows = load_rows(Path(args.input_csv))
    if args.limit > 0:
        input_rows = input_rows[: args.limit]

    violin_rows = load_rows(Path(args.violin_csv))
    by_name, by_pmid = build_violin_indexes(violin_rows)
    _, vo_by_pmid = load_vo_template(Path(args.vo_template_csv))

    output_rows: list[dict[str, str]] = []
    for idx, row in enumerate(input_rows, 1):
        v_id, v_name, v_status, v_conf, v_note = map_violin(row, by_name, by_pmid)
        vo_id, vo_label, vo_status, vo_note = map_vo(row, vo_by_pmid)

        out = dict(row)
        out["Matched VIOLIN ID"] = v_id
        out["Matched VIOLIN Vaccine Name"] = v_name
        out["VIOLIN Match Status"] = v_status
        out["Matched VO ID"] = vo_id
        out["Matched VO Label"] = vo_label
        out["VO Match Status"] = vo_status
        out["Mapping Notes"] = " | ".join([p for p in [v_note, vo_note] if p])
        out["Confidence"] = combined_confidence(v_conf, vo_status)
        output_rows.append(out)

        if idx % 50 == 0:
            print(f"Processed {idx}/{len(input_rows)} rows", flush=True)

    if not output_rows:
        print("No rows processed.")
        return

    fieldnames = list(output_rows[0].keys())
    write_csv(Path(args.out_csv), output_rows, fieldnames)
    write_workbook(Path(args.out_xlsx), output_rows, fieldnames)

    exact_violin = sum(1 for r in output_rows if r.get("VIOLIN Match Status") == "exact")
    possible_violin = sum(1 for r in output_rows if r.get("VIOLIN Match Status") == "possible")
    exact_vo = sum(1 for r in output_rows if r.get("VO Match Status") == "exact")
    possible_vo = sum(1 for r in output_rows if r.get("VO Match Status") == "possible")

    print(f"Input rows: {len(input_rows)}")
    print(f"VIOLIN exact matches: {exact_violin}")
    print(f"VIOLIN possible matches: {possible_violin}")
    print(f"VO exact matches: {exact_vo}")
    print(f"VO possible matches: {possible_vo}")
    print(f"Wrote: {Path(args.out_csv).resolve()}")
    print(f"Wrote: {Path(args.out_xlsx).resolve()}")


if __name__ == "__main__":
    main()
