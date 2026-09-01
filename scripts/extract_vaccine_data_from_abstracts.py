#!/usr/bin/env python3
"""Extract structured vaccine evidence fields from downloaded abstract txt files."""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path


HOST_TERMS = [
    "human",
    "humans",
    "mouse",
    "mice",
    "rat",
    "rats",
    "rabbit",
    "rabbits",
    "cattle",
    "cow",
    "cows",
    "goat",
    "goats",
    "sheep",
    "pig",
    "pigs",
    "guinea pig",
    "monkey",
    "monkeys",
    "macaque",
    "macaques",
]

PATHOGEN_TERMS = [
    "brucella",
    "brucella abortus",
    "brucella melitensis",
    "brucella suis",
    "brucellosis",
]

GENERIC_VACCINE_PHRASES = {
    "vaccine",
    "vaccines",
    "live vaccine",
    "inactivated vaccine",
    "subunit vaccine",
    "dna vaccine",
    "candidate vaccine",
    "experimental vaccine",
    "human vaccine",
    "animal vaccine",
}


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def parse_abstract_txt(file_path: Path) -> dict[str, str]:
    content = file_path.read_text(encoding="utf-8", errors="ignore")
    lines = content.splitlines()

    pmid = ""
    title = ""
    authors = ""
    abstract_lines: list[str] = []
    in_abstract = False

    for line in lines:
        if line.startswith("PMID:"):
            pmid = line.split(":", 1)[1].strip()
        elif line.startswith("Title:"):
            title = line.split(":", 1)[1].strip()
        elif line.startswith("Authors:"):
            authors = line.split(":", 1)[1].strip()
        elif line.startswith("Abstract:"):
            in_abstract = True
            after = line.split(":", 1)[1].strip()
            if after:
                abstract_lines.append(after)
        elif in_abstract:
            abstract_lines.append(line)

    abstract = normalize_space("\n".join(abstract_lines))
    return {
        "pmid": pmid,
        "title": title,
        "authors": authors,
        "abstract": abstract,
    }


def find_terms(text: str, terms: list[str]) -> list[str]:
    lowered = text.lower()
    found: list[str] = []
    for term in terms:
        if re.search(r"\b" + re.escape(term.lower()) + r"\b", lowered):
            found.append(term)
    return sorted(set(found))


def extract_candidate_vaccine_names(text: str) -> list[str]:
    candidates: set[str] = set()

    # Pattern 1: phrase ending with vaccine/vaccines.
    for match in re.finditer(
        r"\b([A-Za-z0-9][A-Za-z0-9+./()\-]*(?:\s+[A-Za-z0-9][A-Za-z0-9+./()\-]*){0,6}\s+vaccines?)\b",
        text,
        flags=re.IGNORECASE,
    ):
        phrase = normalize_space(match.group(1))
        phrase_l = phrase.lower()
        if phrase_l in GENERIC_VACCINE_PHRASES:
            continue
        # Skip highly generic lead words.
        if phrase_l.startswith(("a ", "an ", "the ", "this ", "that ", "these ", "those ")):
            phrase = phrase.split(" ", 1)[1]
            phrase_l = phrase.lower()
        if phrase_l in GENERIC_VACCINE_PHRASES:
            continue
        candidates.add(phrase)

    # Pattern 2: known Brucella vaccine strain names that may not contain "vaccine".
    for match in re.finditer(
        r"\b(RB51|S19|SRB51|Rev\.?\s*1|B\.?\s*abortus\s*(?:strain\s*)?RB51|B\.?\s*abortus\s*(?:strain\s*)?19|B\.?\s*melitensis\s*(?:vaccine\s*strain\s*)?Rev\.?\s*1)\b",
        text,
        flags=re.IGNORECASE,
    ):
        candidates.add(normalize_space(match.group(1)))

    cleaned = [c.strip(" ,.;:") for c in candidates if c.strip(" ,.;:" )]
    cleaned = [c for c in cleaned if c.lower() not in GENERIC_VACCINE_PHRASES]
    return sorted(set(cleaned))


def infer_study_context(text: str) -> str:
    lowered = text.lower()
    preclinical_terms = ["mouse", "mice", "rat", "rabbit", "animal", "challenge model"]
    clinical_terms = ["clinical", "trial", "randomized", "volunteer", "patient", "phase i", "phase ii", "phase iii"]

    has_preclinical = any(term in lowered for term in preclinical_terms)
    has_clinical = any(term in lowered for term in clinical_terms)

    if has_preclinical and has_clinical:
        return "mixed"
    if has_clinical:
        return "clinical"
    if has_preclinical:
        return "preclinical"
    return "unspecified"


def extract_signal_snippets(text: str, keywords: list[str], max_hits: int = 3) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", text)
    hits: list[str] = []
    for sentence in sentences:
        s_l = sentence.lower()
        if any(keyword in s_l for keyword in keywords):
            hits.append(normalize_space(sentence))
        if len(hits) >= max_hits:
            break
    return " | ".join(hits)


def build_extracted_rows(abstract_dir: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    txt_files = sorted([p for p in abstract_dir.glob("*.txt") if p.name != "search_results_non_review.txt"])

    for file_path in txt_files:
        parsed = parse_abstract_txt(file_path)
        text = f"{parsed['title']}\n{parsed['abstract']}"

        candidate_names = extract_candidate_vaccine_names(text)
        host_terms = find_terms(text, HOST_TERMS)
        pathogen_terms = find_terms(text, PATHOGEN_TERMS)
        study_context = infer_study_context(text)
        efficacy = extract_signal_snippets(
            parsed["abstract"],
            ["efficacy", "effective", "protection", "protected", "survival", "reduced", "reduction"],
        )
        safety = extract_signal_snippets(
            parsed["abstract"],
            ["safe", "safety", "adverse", "toxicity", "reactogenic", "side effect", "tolerated"],
        )

        rows.append(
            {
                "pmid": parsed["pmid"],
                "title": parsed["title"],
                "authors": parsed["authors"],
                "abstract": parsed["abstract"],
                "candidate_vaccine_names": " | ".join(candidate_names),
                "candidate_count": str(len(candidate_names)),
                "target_pathogen_mentions": " | ".join(pathogen_terms),
                "host_mentions": " | ".join(host_terms),
                "study_context": study_context,
                "efficacy_signals": efficacy,
                "safety_signals": safety,
            }
        )

    return rows


def write_csv(rows: list[dict[str, str]], out_csv: Path) -> None:
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "pmid",
        "title",
        "authors",
        "abstract",
        "candidate_vaccine_names",
        "candidate_count",
        "target_pathogen_mentions",
        "host_mentions",
        "study_context",
        "efficacy_signals",
        "safety_signals",
    ]

    with out_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract structured vaccine evidence from abstract txt files."
    )
    parser.add_argument("--abstract-dir", default="abstracts", help="Folder of downloaded abstract txt files")
    parser.add_argument(
        "--out-csv",
        default="outputs/extracted_vaccine_records.csv",
        help="Output CSV path",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rows = build_extracted_rows(Path(args.abstract_dir))
    write_csv(rows, Path(args.out_csv))

    with_candidates = sum(1 for row in rows if row["candidate_count"] != "0")
    print(f"Parsed records: {len(rows)}")
    print(f"Records with >=1 vaccine candidate: {with_candidates}")
    print(f"Wrote: {Path(args.out_csv).resolve()}")


if __name__ == "__main__":
    main()
