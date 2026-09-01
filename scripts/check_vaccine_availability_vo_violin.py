#!/usr/bin/env python3
"""Task 3: Check extracted vaccines against VO ontology and VIOLIN database."""

from __future__ import annotations

import argparse
import csv
import json
import re
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path


VO_ID_RE = re.compile(r"\bVO[:_]\d{7}\b", re.IGNORECASE)


def normalize_text(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^a-z0-9]+", "", text)
    return text


def normalize_vo_id(raw: str) -> str:
    raw = raw.strip().upper().replace(":", "_")
    if raw.startswith("VO_") and len(raw) == 10 and raw[3:].isdigit():
        return raw
    return ""


def split_candidate_names(raw: str) -> list[str]:
    if not raw:
        return []
    parts = re.split(r"\s*[|;/]\s*", raw)
    cleaned = [part.strip(" ,.;:\"'()[]") for part in parts if part.strip()]
    blocked = {"not reported", "skip", "n/a", "na"}
    return [p for p in cleaned if p and p.lower() not in blocked]


def collect_vo_ids(raw: str) -> list[str]:
    if not raw:
        return []
    out: list[str] = []
    for hit in VO_ID_RE.findall(raw):
        norm = normalize_vo_id(hit)
        if norm:
            out.append(norm)
    return sorted(set(out))


def get_row_value(row: dict[str, str], *keys: str) -> str:
    for key in keys:
        if key in row and row[key].strip():
            return row[key].strip()
    return ""


@dataclass
class VoLookupResult:
    status: str
    label: str
    iri: str
    method: str


class VoClient:
    def __init__(self, enabled: bool = True, timeout: float = 8.0) -> None:
        self.enabled = enabled
        self.timeout = timeout
        self._cache: dict[str, VoLookupResult] = {}

    def lookup(self, vo_id: str) -> VoLookupResult:
        if vo_id in self._cache:
            return self._cache[vo_id]

        if not self.enabled:
            result = VoLookupResult("not_checked", "", "", "disabled")
            self._cache[vo_id] = result
            return result

        query = urllib.parse.urlencode(
            {
                "q": vo_id,
                "ontology": "vo",
                "exact": "true",
                "type": "class",
            }
        )
        url = f"https://www.ebi.ac.uk/ols4/api/search?{query}"

        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8", errors="ignore"))
        except Exception:
            result = VoLookupResult("lookup_error", "", "", "ols4-search")
            self._cache[vo_id] = result
            return result

        docs = payload.get("response", {}).get("docs", [])
        if not docs:
            result = VoLookupResult("not_found", "", "", "ols4-search")
            self._cache[vo_id] = result
            return result

        best = docs[0]
        result = VoLookupResult(
            status="found",
            label=best.get("label", "") or "",
            iri=best.get("iri", "") or "",
            method="ols4-search",
        )
        self._cache[vo_id] = result
        return result


def load_violin_index(path: Path) -> tuple[dict[str, list[dict[str, str]]], dict[str, list[dict[str, str]]]]:
    by_name: dict[str, list[dict[str, str]]] = defaultdict(list)
    by_vo_id: dict[str, list[dict[str, str]]] = defaultdict(list)

    with path.open("r", encoding="utf-8", errors="ignore", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            names = [
                row.get("c_vaccine_name", ""),
                row.get("c_proper_name", ""),
                row.get("c_brand_name", ""),
            ]
            for name in names:
                norm = normalize_text(name)
                if norm:
                    by_name[norm].append(row)

            raw_vo = row.get("c_vo_id", "")
            for hit in collect_vo_ids(raw_vo):
                by_vo_id[hit].append(row)

    return by_name, by_vo_id


def match_violin_name(name: str, index: dict[str, list[dict[str, str]]]) -> tuple[str, str, str, str]:
    norm = normalize_text(name)
    if not norm:
        return ("no_name", "", "", "empty")

    exact = index.get(norm)
    if exact:
        row = exact[0]
        return (
            "found",
            row.get("c_vaccine_id", ""),
            row.get("c_vaccine_name", ""),
            "exact_normalized",
        )

    if len(norm) >= 6:
        for existing_norm, rows in index.items():
            if norm in existing_norm or existing_norm in norm:
                row = rows[0]
                return (
                    "possible",
                    row.get("c_vaccine_id", ""),
                    row.get("c_vaccine_name", ""),
                    "contains",
                )

    return ("not_found", "", "", "no_match")


def build_rows(
    input_rows: list[dict[str, str]],
    violin_by_name: dict[str, list[dict[str, str]]],
    violin_by_vo_id: dict[str, list[dict[str, str]]],
    vo_client: VoClient,
    vo_sleep_sec: float,
) -> list[dict[str, str]]:
    output_rows: list[dict[str, str]] = []

    for row in input_rows:
        skip_reason = get_row_value(row, "skip_reason", "Skip Reason")
        if skip_reason:
            continue

        source_file = get_row_value(row, "source_file", "Source File")
        pmid = get_row_value(row, "PMID", "pmid")
        title = get_row_value(row, "Title", "title")

        names_raw = " | ".join(
            [
                get_row_value(row, "Standardized Vaccine Name"),
                get_row_value(row, "Proposed Vaccine Name"),
                get_row_value(row, "Extracted Vaccine Name"),
                get_row_value(row, "candidate_vaccine_names"),
            ]
        )
        candidate_names = sorted(set(split_candidate_names(names_raw)))

        vo_raw = " | ".join(
            [
                get_row_value(row, "Vaccine Type"),
                get_row_value(row, "vaccine_type"),
                get_row_value(row, "Notes"),
                get_row_value(row, "Vaccine Introduction"),
            ]
        )
        vo_ids = collect_vo_ids(vo_raw)

        if not candidate_names:
            candidate_names = [""]
        if not vo_ids:
            vo_ids = [""]

        for name in candidate_names:
            violin_status, matched_id, matched_name, match_method = match_violin_name(name, violin_by_name)

            for vo_id in vo_ids:
                vo_status = "no_vo_id"
                vo_label = ""
                vo_iri = ""
                vo_method = ""
                violin_vo_status = "no_vo_id"
                violin_vo_match = ""

                if vo_id:
                    vo_lookup = vo_client.lookup(vo_id)
                    vo_status = vo_lookup.status
                    vo_label = vo_lookup.label
                    vo_iri = vo_lookup.iri
                    vo_method = vo_lookup.method

                    vo_matches = violin_by_vo_id.get(vo_id, [])
                    if vo_matches:
                        violin_vo_status = "found"
                        first = vo_matches[0]
                        violin_vo_match = f"{first.get('c_vaccine_id', '')}:{first.get('c_vaccine_name', '')}"
                    else:
                        violin_vo_status = "not_found"

                    if vo_sleep_sec > 0:
                        time.sleep(vo_sleep_sec)

                output_rows.append(
                    {
                        "source_file": source_file,
                        "PMID": pmid,
                        "Title": title,
                        "candidate_vaccine_name": name,
                        "violin_name_status": violin_status,
                        "violin_name_match_id": matched_id,
                        "violin_name_match": matched_name,
                        "violin_name_match_method": match_method,
                        "vo_id": vo_id,
                        "vo_status": vo_status,
                        "vo_label": vo_label,
                        "vo_iri": vo_iri,
                        "vo_lookup_method": vo_method,
                        "violin_vo_status": violin_vo_status,
                        "violin_vo_match": violin_vo_match,
                    }
                )

    return output_rows


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_summary(path: Path, rows: list[dict[str, str]], input_csv: Path, violin_csv: Path) -> None:
    pair_to_status: dict[tuple[str, str], str] = {}
    for row in rows:
        name = row.get("candidate_vaccine_name", "")
        if not name:
            continue
        key = (row.get("PMID", ""), name)
        status = row.get("violin_name_status", "")
        prev = pair_to_status.get(key, "")
        if prev != "found" and status:
            pair_to_status[key] = status

    unique_pairs = set(pair_to_status.keys())
    name_found = sum(1 for status in pair_to_status.values() if status == "found")
    vo_rows = [r for r in rows if r.get("vo_id")]
    vo_found = sum(1 for r in vo_rows if r.get("vo_status") == "found")
    vo_not_found = sum(1 for r in vo_rows if r.get("vo_status") == "not_found")
    vo_errors = sum(1 for r in vo_rows if r.get("vo_status") == "lookup_error")

    new_candidates = [
        r for r in rows if r.get("candidate_vaccine_name") and r.get("violin_name_status") == "not_found"
    ]
    top_new: dict[str, int] = defaultdict(int)
    for row in new_candidates:
        top_new[row["candidate_vaccine_name"]] += 1

    lines = [
        "# Task3 Vaccine Availability Check",
        "",
        f"- Input extraction file: {input_csv}",
        f"- VIOLIN table: {violin_csv}",
        f"- Checked rows (name x VO combinations): {len(rows)}",
        f"- Unique PMID + vaccine-name pairs: {len(unique_pairs)}",
        f"- PMID + vaccine-name pairs found in VIOLIN: {name_found}",
        f"- PMID + vaccine-name pairs not found in VIOLIN: {len(unique_pairs) - name_found}",
        "",
        "## VO Ontology Check",
        f"- Rows with VO IDs: {len(vo_rows)}",
        f"- VO IDs found in VO ontology: {vo_found}",
        f"- VO IDs not found: {vo_not_found}",
        f"- VO lookup errors: {vo_errors}",
        "",
        "## Top VIOLIN-Missing Candidate Names",
    ]

    for name, count in sorted(top_new.items(), key=lambda kv: (-kv[1], kv[0].lower()))[:20]:
        lines.append(f"- {name} (rows={count})")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Task3 check: availability in VO ontology and VIOLIN database."
    )
    parser.add_argument(
        "--input-csv",
        default="vaccine-data/llm_vaccine_extraction_results.csv",
        help="Input extracted vaccine CSV (Task2 output)",
    )
    parser.add_argument(
        "--violin-vaccine-csv",
        default="violin-data/t_vaccine.csv",
        help="VIOLIN vaccine table",
    )
    parser.add_argument(
        "--out-csv",
        default="outputs/task3_vaccine_availability.csv",
        help="Detailed Task3 output CSV",
    )
    parser.add_argument(
        "--out-summary",
        default="outputs/task3_availability_summary.md",
        help="Task3 markdown summary",
    )
    parser.add_argument(
        "--out-new-candidates",
        default="outputs/new_vaccine_candidates.csv",
        help="Candidate names not found in VIOLIN",
    )
    parser.add_argument(
        "--disable-vo-lookup",
        action="store_true",
        help="Skip online VO lookup and only report IDs found in the extraction rows",
    )
    parser.add_argument(
        "--vo-sleep-sec",
        type=float,
        default=0.0,
        help="Optional delay per VO lookup to reduce API pressure",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    input_csv = Path(args.input_csv)
    violin_csv = Path(args.violin_vaccine_csv)
    out_csv = Path(args.out_csv)
    out_summary = Path(args.out_summary)
    out_new = Path(args.out_new_candidates)

    with input_csv.open("r", encoding="utf-8", errors="ignore", newline="") as handle:
        extracted_rows = list(csv.DictReader(handle))

    violin_by_name, violin_by_vo_id = load_violin_index(violin_csv)

    vo_client = VoClient(enabled=not args.disable_vo_lookup)
    rows = build_rows(
        extracted_rows,
        violin_by_name,
        violin_by_vo_id,
        vo_client,
        args.vo_sleep_sec,
    )

    fields = [
        "source_file",
        "PMID",
        "Title",
        "candidate_vaccine_name",
        "violin_name_status",
        "violin_name_match_id",
        "violin_name_match",
        "violin_name_match_method",
        "vo_id",
        "vo_status",
        "vo_label",
        "vo_iri",
        "vo_lookup_method",
        "violin_vo_status",
        "violin_vo_match",
    ]
    write_csv(out_csv, rows, fields)

    new_rows = [
        {
            "candidate_vaccine_name": r["candidate_vaccine_name"],
            "PMID": r["PMID"],
            "Title": r["Title"],
            "source_file": r["source_file"],
            "vo_id": r["vo_id"],
            "vo_status": r["vo_status"],
        }
        for r in rows
        if r["candidate_vaccine_name"] and r["violin_name_status"] == "not_found"
    ]
    write_csv(
        out_new,
        new_rows,
        ["candidate_vaccine_name", "PMID", "Title", "source_file", "vo_id", "vo_status"],
    )

    write_summary(out_summary, rows, input_csv, violin_csv)

    unique_names = sorted({r["candidate_vaccine_name"] for r in rows if r["candidate_vaccine_name"]})
    in_violin = sorted({r["candidate_vaccine_name"] for r in rows if r["violin_name_status"] == "found"})
    print(f"Input rows: {len(extracted_rows)}")
    print(f"Task3 rows written: {len(rows)}")
    print(f"Unique candidate names: {len(unique_names)}")
    print(f"Candidate names found in VIOLIN: {len(in_violin)}")
    print(f"Wrote: {out_csv.resolve()}")
    print(f"Wrote: {out_new.resolve()}")
    print(f"Wrote: {out_summary.resolve()}")


if __name__ == "__main__":
    main()