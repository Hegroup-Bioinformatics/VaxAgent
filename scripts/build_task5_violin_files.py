#!/usr/bin/env python3
"""Task 5: convert Rooney-reviewed Brucella vaccine candidates into VIOLIN loading files.

Reads the manually reviewed candidate workbook (read-only), deduplicates candidate
rows into distinct vaccine records using Rooney's standardized vaccine names and
combination-vaccine decisions, resolves host IDs against violin-data/t_host.csv,
verifies/creates reference records via PubMed E-utils, and writes new
t_reference.csv- and t_vaccine.csv-style files for loading. Existing VIOLIN files
and the reviewed workbook are never modified.
"""

from __future__ import annotations

import argparse
import csv
import html
import re
import sys
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

import openpyxl
import requests

BRUCELLA_PATHOGEN_ID = "8"
EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

REFERENCE_FIELDS = [
    "c_reference_id", "c_pathogen_id", "c_reference_name", "c_reference_type",
    "c_authors", "c_title", "c_year", "c_volume", "c_issue", "c_pages",
    "c_journal_book_name", "c_publisher", "c_publisher_location", "c_book_editors",
    "c_isbn", "c_university", "c_university_location", "c_degree", "c_pmid",
    "c_url", "c_file_name", "c_gene_id", "c_curation_flag", "c_vaxjo_id",
    "c_dv_plasmid_id", "c_vaxvec_id", "t_referencecol", "c_vaxanimodel_id", "c_comment",
]

VACCINE_FIELDS = [
    "c_vaccine_id", "c_vaccine_name", "c_type", "c_is_combination_vaccine",
    "c_description", "c_adjuvant", "c_storage", "c_pathogen_id", "c_virulence",
    "c_preparation", "c_brand_name", "c_full_text", "c_antigen", "c_curation_flag",
    "c_vector", "c_proper_name", "c_manufacturer", "c_contraindication", "c_status",
    "c_location_licensed", "c_host_species", "c_route", "c_vo_id", "c_usage_age",
    "c_model_host", "c_preservative", "c_allergen", "c_preparation_vo_id",
    "c_host_species2", "c_cvx_code", "c_cvx_desc",
]

# Curated cell-line / cell-type -> host common name (None = no confident animal host).
CELL_LINE_MAP = {
    "raw264.7": "Mouse", "raw 264.7": "Mouse", "j774": "Mouse",
    "hela": "Human", "hek293": "Human", "hek 293": "Human",
    "thp-1": "Human", "thp1": "Human",
    "cos-7": None, "cos7": None,
    "sf9": None, "sf21": None, "trichoplusia ni": None, "spodoptera": None,
    "vero": None,
}

BLANK_VALUES = {"", "not reported", "none", "n/a", "not applicable"}


def clean(value) -> str:
    if value is None:
        return ""
    return html.unescape(str(value).strip())


def is_blank(value) -> bool:
    return clean(value).lower() in BLANK_VALUES


def normalize_key(value: str) -> str:
    text = clean(value).lower()
    text = re.sub(r"[^a-z0-9 ]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_combo(value: str) -> str:
    text = clean(value)
    if "-->" in text:
        text = text.split("-->")[-1]
    text = text.split(" - ")[0].split("(")[0]
    text = re.sub(r"[^a-zA-Z]", "", text).lower()
    return "Yes" if text == "yes" else "No"


def normalize_status(value: str) -> str:
    text = clean(value).lower()
    if "research" in text:
        return "Research"
    if "licensed" in text:
        return "Licensed"
    return ""


def normalize_type(value: str) -> str:
    if is_blank(value):
        return ""
    parts = [p.strip() for p in clean(value).split(";") if p.strip()]
    cleaned: list[str] = []
    seen: set[str] = set()
    for part in parts:
        part = re.sub(r"^VO:\d+:\s*", "", part).strip()
        if not part:
            continue
        label = part[0].upper() + part[1:] if part else part
        key = label.lower()
        if key not in seen:
            seen.add(key)
            cleaned.append(label)
    return " + ".join(cleaned)


def split_multi(value: str) -> list[str]:
    if is_blank(value):
        return []
    text = re.sub(r"\([^)]*\)", "", clean(value))
    parts = re.split(r"\s*;\s*", text)
    return [p.strip() for p in parts if p.strip()]


def load_reviewed_rows(path: Path) -> list[dict[str, str]]:
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb["candidate_existing_match"]
    rows = list(ws.iter_rows(values_only=True))
    header = rows[0]
    data: list[dict[str, str]] = []
    for raw in rows[1:]:
        if any(v not in (None, "") for v in raw):
            data.append({h: raw[i] if i < len(raw) else "" for i, h in enumerate(header)})
    return data


def load_csv_rows(path: Path) -> list[dict[str, str]]:
    csv.field_size_limit(sys.maxsize)
    with path.open("r", encoding="utf-8", errors="ignore", newline="") as handle:
        return list(csv.DictReader(handle))


class HostIndex:
    def __init__(self, host_rows: list[dict[str, str]]):
        self.by_sci: dict[str, tuple[str, str]] = {}
        self.by_name: dict[str, tuple[str, str]] = {}
        self.by_genus: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for row in host_rows:
            hid = clean(row.get("c_host_id", ""))
            name = clean(row.get("c_host_name", ""))
            sci = clean(row.get("c_scientific_name", "")).lower()
            if name and name.lower() != "none":
                self.by_name[name.lower()] = (hid, name)
            if sci and sci != "none":
                self.by_sci[sci] = (hid, name)
                genus = sci.split()[0]
                self.by_genus[genus].append((hid, name))

    def resolve_token(self, token: str) -> tuple[str, str] | None:
        text = re.sub(r"[^a-z ]", "", token.lower())
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            return None
        if text in self.by_sci:
            return self.by_sci[text]
        if text in self.by_name:
            return self.by_name[text]
        genus = text.split()[0]
        if genus in self.by_genus and len({h for h, _ in self.by_genus[genus]}) == 1:
            return self.by_genus[genus][0]
        for key, host_name in CELL_LINE_MAP.items():
            if key in text:
                if host_name is None:
                    return None
                return self.by_name.get(host_name.lower())
        if "mouse" in text or "mice" in text or "murine" in text:
            return self.by_name.get("mouse")
        if "human" in text:
            return self.by_name.get("human")
        if "rabbit" in text:
            return self.by_name.get("rabbit")
        if "guinea pig" in text:
            return self.by_name.get("guinea pig")
        if "goat" in text:
            return self.by_name.get("goat")
        if "cattle" in text or "bovine" in text or "calv" in text or "heifer" in text:
            return self.by_name.get("cattle")
        return None

    def resolve_recipients(self, raw: str) -> list[tuple[str, str]]:
        resolved: list[tuple[str, str]] = []
        seen: set[str] = set()
        for token in split_multi(raw):
            hit = self.resolve_token(token)
            if hit and hit[0] not in seen:
                seen.add(hit[0])
                resolved.append(hit)
        return resolved

    def resolve_model_text(self, raw: str) -> str:
        names: list[str] = []
        for token in split_multi(raw):
            hit = self.resolve_token(token)
            if hit and hit[1] not in names:
                names.append(hit[1])
        return "; ".join(names)


def text_or_empty(elem, path: str) -> str:
    node = elem.find(path)
    return node.text.strip() if node is not None and node.text else ""


def extract_authors(article) -> list[str]:
    authors: list[str] = []
    for author in article.findall("./MedlineCitation/Article/AuthorList/Author"):
        collective = text_or_empty(author, "./CollectiveName")
        if collective:
            authors.append(collective)
            continue
        last = text_or_empty(author, "./LastName")
        initials = text_or_empty(author, "./Initials")
        if last and initials:
            authors.append(f"{last} {initials}")
        elif last:
            authors.append(last)
    return authors


def extract_pub_date(article) -> tuple[str, str]:
    """Return (full_date_text, four_digit_year)."""
    issue = article.find("./MedlineCitation/Article/Journal/JournalIssue/PubDate")
    if issue is None:
        return "", ""
    year = text_or_empty(issue, "./Year")
    month = text_or_empty(issue, "./Month")
    day = text_or_empty(issue, "./Day")
    if not year:
        medline_date = text_or_empty(issue, "./MedlineDate")
        match = re.search(r"\d{4}", medline_date)
        year = match.group(0) if match else ""
        return medline_date, year
    parts = [p for p in [year, month, day] if p]
    return " ".join(parts), year


def parse_article(article) -> dict[str, str]:
    full_date, year = extract_pub_date(article)
    return {
        "authors": extract_authors(article),
        "title": text_or_empty(article, "./MedlineCitation/Article/ArticleTitle"),
        "year_full": full_date,
        "year": year,
        "volume": text_or_empty(article, "./MedlineCitation/Article/Journal/JournalIssue/Volume"),
        "issue": text_or_empty(article, "./MedlineCitation/Article/Journal/JournalIssue/Issue"),
        "pages": text_or_empty(article, "./MedlineCitation/Article/Pagination/MedlinePgn"),
        "journal": text_or_empty(article, "./MedlineCitation/Article/Journal/Title"),
    }


def fetch_pubmed_metadata(pmids: list[str]) -> dict[str, dict[str, str]]:
    records: dict[str, dict[str, str]] = {}
    for i in range(0, len(pmids), 200):
        chunk = pmids[i:i + 200]
        response = requests.get(
            f"{EUTILS_BASE}/efetch.fcgi",
            params={"db": "pubmed", "id": ",".join(chunk), "retmode": "xml"},
            timeout=90,
        )
        response.raise_for_status()
        root = ET.fromstring(response.text)
        for article in root.findall(".//PubmedArticle"):
            pmid = text_or_empty(article, "./MedlineCitation/PMID")
            if pmid:
                records[pmid] = parse_article(article)
        print(f"  fetched metadata for {min(i + 200, len(pmids))}/{len(pmids)} PMIDs", flush=True)
        time.sleep(0.35)
    return records


def build_reference_name(authors: list[str], year: str, used_names: set[str]) -> str:
    if not authors or not year:
        return ""
    first_last = authors[0].split(" ")[0] if authors[0] else ""
    if not first_last:
        return ""
    base = f"{first_last} et al, {year}" if len(authors) > 1 else f"{first_last}, {year}"
    name = base
    suffix_ord = ord("a")
    while name in used_names:
        name = f"{base[:-len(year)]}{year}{chr(suffix_ord)}"
        suffix_ord += 1
    used_names.add(name)
    return name


def build_description(rep_row: dict[str, str], ref_ids: list[str]) -> str:
    parts: list[str] = []
    intro = clean(rep_row.get("Vaccine Introduction", ""))
    if not is_blank(intro):
        parts.append(intro)
    efficacy = clean(rep_row.get("Vaccine Efficacy", ""))
    if not is_blank(efficacy):
        parts.append(f"Efficacy: {efficacy}")
    description = " ".join(parts).strip()
    tags = "".join(f"[Ref:{rid}]" for rid in ref_ids if rid)
    if tags:
        description = f"{description} {tags}".strip()
    return description


def group_rows(rows: list[dict[str, str]]) -> list[list[dict[str, str]]]:
    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    order: list[str] = []
    for row in rows:
        key = normalize_key(row.get("Standardized Vaccine Name", ""))
        if key not in groups:
            order.append(key)
        groups[key].append(row)
    return [groups[key] for key in order]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Task 5 VIOLIN loading files from Rooney-reviewed candidates.")
    parser.add_argument("--input-xlsx", default="outputs/new_brucella_vaccines_rooney_reviewed.xlsx")
    parser.add_argument("--reference-csv", default="violin-data/t_reference.csv")
    parser.add_argument("--vaccine-csv", default="violin-data/t_vaccine.csv")
    parser.add_argument("--host-csv", default="violin-data/t_host.csv")
    parser.add_argument("--out-references", default="outputs/task5_new_references.csv")
    parser.add_argument("--out-vaccines", default="outputs/task5_new_vaccines.csv")
    parser.add_argument("--out-summary", default="outputs/task5_summary.md")
    parser.add_argument("--skip-pubmed", action="store_true", help="Skip PubMed lookups (for dry runs / testing).")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    reviewed_rows = load_reviewed_rows(Path(args.input_xlsx))
    existing_references = load_csv_rows(Path(args.reference_csv))
    existing_vaccines = load_csv_rows(Path(args.vaccine_csv))
    host_rows = load_csv_rows(Path(args.host_csv))
    host_index = HostIndex(host_rows)

    existing_ref_ids = [int(r["c_reference_id"]) for r in existing_references if r["c_reference_id"].strip().isdigit()]
    existing_vaccine_ids = [int(r["c_vaccine_id"]) for r in existing_vaccines if r["c_vaccine_id"].strip().isdigit()]
    next_ref_id = max(existing_ref_ids, default=0) + 1
    next_vaccine_id = max(existing_vaccine_ids, default=0) + 1

    existing_pmid_to_ref = {
        clean(r["c_pmid"]): r["c_reference_id"]
        for r in existing_references
        if r.get("c_pathogen_id", "").strip() == BRUCELLA_PATHOGEN_ID and clean(r.get("c_pmid", ""))
    }
    existing_ref_names = {clean(r.get("c_reference_name", "")) for r in existing_references}

    all_pmids = sorted({clean(r.get("PMID", "")) for r in reviewed_rows if clean(r.get("PMID", ""))})
    new_pmids = [p for p in all_pmids if p not in existing_pmid_to_ref]

    print(f"Reviewed candidate rows: {len(reviewed_rows)}")
    print(f"Unique PMIDs: {len(all_pmids)} ({len(new_pmids)} need new reference rows)")

    pubmed_metadata: dict[str, dict[str, str]] = {}
    if new_pmids and not args.skip_pubmed:
        print("Querying PubMed E-utils for new reference metadata...")
        pubmed_metadata = fetch_pubmed_metadata(new_pmids)

    new_reference_rows: list[dict[str, str]] = []
    pmid_to_ref: dict[str, str] = dict(existing_pmid_to_ref)
    unresolved_pmids: list[str] = []
    used_ref_names = set(existing_ref_names)

    for pmid in new_pmids:
        meta = pubmed_metadata.get(pmid, {})
        authors = meta.get("authors", [])
        year = meta.get("year", "")
        ref_name = build_reference_name(authors, year, used_ref_names)
        if not meta or not ref_name:
            unresolved_pmids.append(pmid)

        ref_id = str(next_ref_id)
        next_ref_id += 1
        pmid_to_ref[pmid] = ref_id

        row = {field: "" for field in REFERENCE_FIELDS}
        row.update({
            "c_reference_id": ref_id,
            "c_pathogen_id": BRUCELLA_PATHOGEN_ID,
            "c_reference_name": ref_name,
            "c_reference_type": "journal" if meta else "",
            "c_authors": ", ".join(authors),
            "c_title": meta.get("title", ""),
            "c_year": meta.get("year_full", ""),
            "c_volume": meta.get("volume", ""),
            "c_issue": meta.get("issue", ""),
            "c_pages": meta.get("pages", ""),
            "c_journal_book_name": meta.get("journal", ""),
            "c_pmid": pmid,
            "c_gene_id": "0",
            "c_curation_flag": "10",
            "c_vaxjo_id": "0",
            "c_dv_plasmid_id": "0",
            "c_vaxvec_id": "0",
        })
        new_reference_rows.append(row)

    groups = group_rows(reviewed_rows)
    print(f"Deduplicated into {len(groups)} distinct vaccine records (from {len(reviewed_rows)} candidate rows)")

    new_vaccine_rows: list[dict[str, str]] = []
    host_recipient_resolved = 0
    host_recipient_total = 0
    model_host_resolved = 0
    model_host_total = 0
    pathogen_conflicts: list[str] = []

    for group in groups:
        rep = group[0]
        std_name = clean(rep.get("Standardized Vaccine Name", ""))
        if not std_name:
            continue

        combo_values = {normalize_combo(r.get("Combination Vaccine", "")) for r in group}
        is_combo = "Yes" if "Yes" in combo_values else "No"

        pathogens = {clean(r.get("Pathogen", "")) for r in group if not is_blank(r.get("Pathogen", ""))}
        if len(pathogens) > 1:
            pathogen_conflicts.append(f"{std_name}: {sorted(pathogens)}")

        group_pmids: list[str] = []
        for r in group:
            pmid = clean(r.get("PMID", ""))
            if pmid and pmid not in group_pmids:
                group_pmids.append(pmid)
        ref_ids = [pmid_to_ref[p] for p in group_pmids if p in pmid_to_ref]

        recipient_raw = next((r.get("Target Vaccine Recipient or Host", "") for r in group if not is_blank(r.get("Target Vaccine Recipient or Host", ""))), "")
        host_recipient_total += 1
        recipients = host_index.resolve_recipients(recipient_raw) if recipient_raw else []
        if recipients:
            host_recipient_resolved += 1
        host_species = recipients[0][0] if len(recipients) > 0 else "0"
        host_species2 = recipients[1][0] if len(recipients) > 1 else "0"

        model_raw = next((r.get("Lab Model or Cell", "") for r in group if not is_blank(r.get("Lab Model or Cell", ""))), "")
        model_host_total += 1
        model_text = host_index.resolve_model_text(model_raw) if model_raw else ""
        if model_text:
            model_host_resolved += 1

        vaccine_id = str(next_vaccine_id)
        next_vaccine_id += 1

        row = {field: "" for field in VACCINE_FIELDS}
        row.update({
            "c_vaccine_id": vaccine_id,
            "c_vaccine_name": std_name,
            "c_type": normalize_type(rep.get("Vaccine Type", "")),
            "c_is_combination_vaccine": is_combo,
            "c_description": build_description(rep, ref_ids),
            "c_adjuvant": "" if is_blank(rep.get("Adjuvant", "")) else clean(rep.get("Adjuvant", "")),
            "c_pathogen_id": BRUCELLA_PATHOGEN_ID,
            "c_preparation": "" if is_blank(rep.get("Vaccine Formulation", "")) else clean(rep.get("Vaccine Formulation", "")),
            "c_antigen": "" if is_blank(rep.get("Gene or Protein Antigen", "")) else clean(rep.get("Gene or Protein Antigen", "")),
            "c_curation_flag": "10",
            "c_status": normalize_status(rep.get("Vaccine Development Stage", "")),
            "c_host_species": host_species,
            "c_host_species2": host_species2,
            "c_model_host": model_text,
        })
        new_vaccine_rows.append(row)

    Path(args.out_references).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out_references, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REFERENCE_FIELDS)
        writer.writeheader()
        writer.writerows(new_reference_rows)

    with open(args.out_vaccines, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=VACCINE_FIELDS)
        writer.writeheader()
        writer.writerows(new_vaccine_rows)

    summary_lines = [
        "# Task 5 Summary: Brucella Vaccine Candidates -> VIOLIN Loading Files",
        "",
        f"- Source workbook: `{args.input_xlsx}` (sheet `candidate_existing_match`, read-only, not modified)",
        f"- Candidate rows reviewed: {len(reviewed_rows)}",
        f"- Distinct vaccine records after dedup by Standardized Vaccine Name: {len(new_vaccine_rows)}",
        f"- Unique PMIDs referenced: {len(all_pmids)}",
        f"- New reference rows created: {len(new_reference_rows)} (new c_reference_id {next_ref_id - len(new_reference_rows)}-{next_ref_id - 1})",
        f"- New vaccine rows created: {len(new_vaccine_rows)} (new c_vaccine_id {next_vaccine_id - len(new_vaccine_rows)}-{next_vaccine_id - 1})",
        f"- PMIDs already present in t_reference.csv for pathogen_id=8: {len(all_pmids) - len(new_pmids)}",
        "",
        "## Host resolution",
        f"- Target Vaccine Recipient or Host resolved: {host_recipient_resolved}/{host_recipient_total} vaccine groups",
        f"- Lab Model or Cell resolved to c_model_host: {model_host_resolved}/{model_host_total} vaccine groups",
        "",
        "## Items flagged for manual review",
        f"- PMIDs with unresolved/incomplete PubMed metadata ({len(unresolved_pmids)}): {', '.join(unresolved_pmids) if unresolved_pmids else 'none'}",
        f"- Vaccine groups with inconsistent Pathogen text across constituent rows ({len(pathogen_conflicts)}, informational only since c_pathogen_id is fixed at 8 per prompt instructions):",
    ]
    for note in pathogen_conflicts:
        summary_lines.append(f"  - {note}")
    summary_lines.append("")
    summary_lines.append("## Notes")
    summary_lines.append("- c_pathogen_id is fixed at 8 (Brucella spp.) for all new records, per prompts/vaccine-to-violin_prompt.txt.")
    summary_lines.append("- Reference tags were embedded directly in c_description at generation time (format `[Ref:c_reference_id]`), since this pipeline has no separate database-load step to defer Task 5 Step 4 to.")
    summary_lines.append("- Fields with no confirmable source data (c_full_text, c_vo_id, c_brand_name, c_manufacturer, c_route, c_usage_age, c_preservative, c_allergen, c_cvx_code, c_cvx_desc, c_virulence, c_vector, c_proper_name, c_contraindication, c_location_licensed, c_storage, c_preparation_vo_id) were left blank rather than guessed.")

    with open(args.out_summary, "w", encoding="utf-8") as handle:
        handle.write("\n".join(summary_lines) + "\n")

    print(f"Wrote {len(new_reference_rows)} reference rows -> {args.out_references}")
    print(f"Wrote {len(new_vaccine_rows)} vaccine rows -> {args.out_vaccines}")
    print(f"Wrote summary -> {args.out_summary}")


if __name__ == "__main__":
    main()
