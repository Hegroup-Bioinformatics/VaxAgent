#!/usr/bin/env python3
"""Preview-only Q1/Q2/Q3 review of outputs/new_brucella_vaccines_reviewed.xlsx.

Calls an LLM per row (with sibling-cluster context for rows that share a
Standardized Vaccine Name) to propose:
  - Final New Vaccine Status (Confirmed New | Duplicate/Existing Candidate |
    Exclude | Uncertain - Needs Review)
  - AI Confidence (High | Medium | Low)
  - Rooney Conflict (Yes | No | Not Applicable)
  - Evidence Source For Q3 (sibling-row | vo-violin-match | both | none)
  - Review Reason

`Needs Human Check` is computed deterministically in this script (not by the
LLM) from the rules agreed with the user, for reproducibility.

This script NEVER writes to new_brucella_vaccines.xlsx, the RK-annotated input,
or new_brucella_vaccines_reviewed.xlsx. It only writes a new preview report.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
from openai import APIError, OpenAI, RateLimitError

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
REVIEWED_XLSX = PROJECT_ROOT / "outputs" / "new_brucella_vaccines_reviewed.xlsx"
ABSTRACTS_CSV = PROJECT_ROOT / "abstracts" / "pmid_abstract_non_review.csv"
PROMPT_FILE = PROJECT_ROOT / "prompts" / "vaccine-new-candidate-review_prompt.txt"
ENV_FILE = PROJECT_ROOT / ".env"
DEFAULT_PREVIEW_OUT = PROJECT_ROOT / "outputs" / "task6_new_vaccine_review_preview.csv"

RESPONSE_FIELDS = [
    "PMID",
    "Vaccine Row",
    "Q1 Vaccine Purpose",
    "Q2 Distinct Construct",
    "Q3 Same As",
    "Final New Vaccine Status",
    "Final Standardized Vaccine Name",
    "AI Confidence",
    "Rooney Conflict",
    "Evidence Source For Q3",
    "Review Reason",
]

MISSING_TOKENS = {"", "nan", "none", "not reported", "not applicable", "n/a"}


def is_missing(value: object) -> bool:
    return str(value).strip().lower() in MISSING_TOKENS


def load_env_file(env_file: Path) -> bool:
    if not env_file.exists():
        return False
    for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())
    return True


def load_system_prompt(prompt_file: Path) -> str:
    text = prompt_file.read_text(encoding="utf-8")
    match = re.search(r'SYSTEM_PROMPT\s*=\s*"""(.*?)"""', text, re.DOTALL)
    return match.group(1).strip() if match else text.strip()


def normalize_name(value: object) -> str:
    return re.sub(r"\s+", " ", str(value).strip().lower())


def build_clusters(df: pd.DataFrame) -> dict[str, list[dict]]:
    """Group rows by normalized Standardized Vaccine Name."""
    clusters: dict[str, list[dict]] = defaultdict(list)
    for _, row in df.iterrows():
        key = normalize_name(row["Standardized Vaccine Name"])
        clusters[key].append(row.to_dict())
    return clusters


def format_sibling(row: dict, self_key: tuple) -> str:
    key = (row["PMID"], row["Vaccine Row"])
    marker = " (THIS ROW)" if key == self_key else ""
    parts = [
        f"PMID {row['PMID']} / Vaccine Row {row['Vaccine Row']}{marker}",
        f"Extracted Vaccine Name: {row.get('Extracted Vaccine Name', '')}",
        f"Gene or Protein Antigen: {row.get('Gene or Protein Antigen', '')}",
        f"Adjuvant: {row.get('Adjuvant', '')}",
        f"Combination Vaccine: {row.get('Combination Vaccine', '')}",
        f"Pathogen: {row.get('Pathogen', '')}",
    ]
    return " | ".join(parts)


def build_payload(row: dict, siblings: list[dict], abstract_text: str) -> str:
    self_key = (row["PMID"], row["Vaccine Row"])
    lines = ["Structured record:"]
    for field in [
        "PMID",
        "Vaccine Row",
        "Title",
        "Extracted Vaccine Name",
        "Make Vaccine Name",
        "Standardized Vaccine Name",
        "ROONEY NOTES",
        "Combination Vaccine",
        "Combination Review Note",
        "Pathogen",
        "Target Vaccine Recipient or Host",
        "Lab Model or Cell",
        "Vaccine Type",
        "Vaccine Development Stage",
        "Adjuvant",
        "Gene or Protein Antigen",
        "Vaccine Formulation",
        "Vaccine Efficacy",
        "In VO or VIOLIN",
        "Matched VIOLIN Vaccine Name",
        "Matched VIOLIN VO ID",
        "VIOLIN Match Score",
        "Matched VO ID",
        "Matched VO Label",
        "VO Match Score",
        "Match Confidence",
    ]:
        lines.append(f"{field}: {row.get(field, '')}")

    others = [s for s in siblings if (s["PMID"], s["Vaccine Row"]) != self_key]
    lines.append("")
    if others:
        lines.append(
            f"Sibling rows sharing this normalized Standardized Vaccine Name ({len(others)} other row(s)):"
        )
        for sib in others:
            lines.append(f"- {format_sibling(sib, self_key)}")
    else:
        lines.append("Sibling rows sharing this normalized Standardized Vaccine Name: none.")

    lines.append("")
    lines.append("Publication abstract:")
    lines.append(abstract_text if abstract_text else "Abstract text not available.")
    return "\n".join(lines)


def parse_response(text: str) -> dict[str, str]:
    result = {f: "" for f in RESPONSE_FIELDS}
    label_pattern = re.compile(
        r"^(" + "|".join(re.escape(f) for f in RESPONSE_FIELDS) + r")\s*:\s*(.*)",
        re.IGNORECASE,
    )
    current_label = None
    current_lines: list[str] = []
    for line in text.splitlines():
        m = label_pattern.match(line)
        if m:
            if current_label is not None:
                result[current_label] = " ".join(current_lines).strip()
            raw_label = m.group(1)
            current_label = next(
                (f for f in RESPONSE_FIELDS if f.lower() == raw_label.lower()), raw_label
            )
            current_lines = [m.group(2).strip()]
        elif current_label is not None:
            current_lines.append(line.strip())
    if current_label is not None:
        result[current_label] = " ".join(current_lines).strip()
    return result


def process_row(
    row: dict,
    siblings: list[dict],
    abstract_text: str,
    client: OpenAI,
    model: str,
    system_prompt: str,
) -> dict[str, str]:
    payload = build_payload(row, siblings, abstract_text)
    reply = ""
    finish_reason = ""
    for attempt in range(1, 4):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": payload},
                ],
                max_tokens=900,
            )
            choice = response.choices[0]
            reply = choice.message.content or ""
            finish_reason = choice.finish_reason or ""
            if reply.strip():
                break
        except RateLimitError:
            time.sleep(20 * attempt)
        except APIError as exc:
            if attempt == 3:
                reply = f"Review Reason: API error after 3 attempts - {exc}"
            time.sleep(5)
    parsed = parse_response(reply)
    if not parsed.get("Final New Vaccine Status"):
        parsed["Final New Vaccine Status"] = "Uncertain - Needs Review"
    if not parsed.get("AI Confidence"):
        parsed["AI Confidence"] = "Low"
    if not parsed.get("Final Standardized Vaccine Name"):
        parsed["Final Standardized Vaccine Name"] = row.get("Standardized Vaccine Name", "")
    if not parsed.get("Review Reason"):
        parsed["Review Reason"] = (
            f"Model response missing structured Review Reason "
            f"(finish_reason={finish_reason!r}, raw_reply={reply[:300]!r})"
        )
    parsed["PMID"] = str(row["PMID"])
    parsed["Vaccine Row"] = str(row["Vaccine Row"])
    return parsed


ROW_REF_PATTERN = re.compile(r"^ROW:(\d+)-(\d+)$", re.IGNORECASE)


def normalize_duplicate_clusters(results: list[dict]) -> list[dict]:
    """Collapse Q3 "Duplicate/Existing Candidate" references within a run into a single
    deterministic canonical representative per connected group, instead of allowing
    rows to point circularly/inconsistently at each other. Does not alter Q1/Q2 fields.
    """
    key_index = {(r["PMID"], r["Vaccine Row"]): i for i, r in enumerate(results)}
    parent = {k: k for k in key_index}

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for r in results:
        if r.get("Final New Vaccine Status") != "Duplicate/Existing Candidate":
            continue
        m = ROW_REF_PATTERN.match(str(r.get("Q3 Same As", "")).strip())
        if not m:
            continue
        target = (int(m.group(1)), int(m.group(2)))
        if target not in key_index:
            continue  # referenced row wasn't included in this run; can't resolve deterministically
        union((r["PMID"], r["Vaccine Row"]), target)

    components: dict[tuple, list[tuple]] = defaultdict(list)
    for k in key_index:
        components[find(k)].append(k)

    for members in components.values():
        if len(members) < 2:
            continue
        canonical = min(members)
        others = sorted(m for m in members if m != canonical)
        for member in members:
            r = results[key_index[member]]
            if member == canonical:
                if r.get("Final New Vaccine Status") == "Duplicate/Existing Candidate":
                    r["Final New Vaccine Status"] = "Confirmed New"
                r["Q3 Same As"] = "None"
                r["Review Reason"] = (
                    str(r.get("Review Reason", "")).rstrip()
                    + f" [Auto-normalized: canonical representative of duplicate cluster with {others}.]"
                )
            else:
                r["Final New Vaccine Status"] = "Duplicate/Existing Candidate"
                r["Q3 Same As"] = f"ROW:{canonical[0]}-{canonical[1]}"
                r["Review Reason"] = (
                    str(r.get("Review Reason", "")).rstrip()
                    + f" [Auto-normalized: reassigned to canonical representative ROW:{canonical[0]}-{canonical[1]}.]"
                )
    return results


def compute_needs_human_check(row: dict, llm: dict, cluster_size: int) -> str:
    """No" only if: Confirmed New + High confidence + no cluster + no conflict + no missing field."""
    status = llm.get("Final New Vaccine Status", "")
    confidence = llm.get("AI Confidence", "").strip().lower()
    rooney_conflict = llm.get("Rooney Conflict", "").strip().lower()

    if status != "Confirmed New":
        return "Yes"
    if confidence != "high":
        return "Yes"
    if rooney_conflict == "yes":
        return "Yes"
    if cluster_size > 1:
        return "Yes"
    for field in ("Pathogen", "Vaccine Type", "Gene or Protein Antigen"):
        if is_missing(row.get(field, "")):
            return "Yes"
    return "No"


def main() -> None:
    parser = argparse.ArgumentParser(description="Preview-only Q1/Q2/Q3 review (no workbook writes).")
    parser.add_argument("--limit", type=int, default=0, help="Only process first N rows (0 = all 760)")
    parser.add_argument(
        "--only",
        default=None,
        help="Comma-separated PMID:VaccineRow pairs to process (e.g. '30885303:2,30885303:3'); overrides --limit",
    )
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--model", default=None)
    parser.add_argument("--out", default=str(DEFAULT_PREVIEW_OUT))
    args = parser.parse_args()

    if not load_env_file(ENV_FILE):
        print(f"ERROR: Could not load .env from {ENV_FILE}", file=sys.stderr)
        sys.exit(1)
    api_key = os.environ.get("OPENAI_API_KEY")
    api_base = os.environ.get("OPENAI_API_BASE")
    model = args.model or os.environ.get("MODEL", "gpt-4o-mini")
    if not api_key or not api_base:
        print("ERROR: OPENAI_API_KEY and OPENAI_API_BASE must be set in .env", file=sys.stderr)
        sys.exit(1)

    system_prompt = load_system_prompt(PROMPT_FILE)
    df = pd.read_excel(REVIEWED_XLSX)
    abstracts = pd.read_csv(ABSTRACTS_CSV)
    abstract_map = dict(zip(abstracts["PMID"].astype(str), abstracts["Abstract"].astype(str)))

    clusters = build_clusters(df)
    rows = df.to_dict("records")
    if args.only:
        wanted = set()
        for pair in args.only.split(","):
            pmid_str, row_str = pair.split(":")
            wanted.add((int(pmid_str.strip()), int(row_str.strip())))
        rows = [r for r in rows if (r["PMID"], r["Vaccine Row"]) in wanted]
    elif args.limit > 0:
        rows = rows[: args.limit]

    thread_local = threading.local()

    def get_client() -> OpenAI:
        if not hasattr(thread_local, "client"):
            thread_local.client = OpenAI(api_key=api_key, base_url=api_base)
        return thread_local.client

    results: list[dict] = []
    total = len(rows)
    print(f"Reviewing {total} row(s) with model={model}, workers={args.workers}")

    def worker(row: dict) -> tuple[dict, dict]:
        key = normalize_name(row["Standardized Vaccine Name"])
        siblings = clusters.get(key, [row])
        abstract_text = abstract_map.get(str(row["PMID"]), "")
        llm = process_row(row, siblings, abstract_text, get_client(), model, system_prompt)
        return row, llm

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(worker, row): row for row in rows}
        done = 0
        for future in as_completed(futures):
            row = futures[future]
            try:
                row, llm = future.result()
            except Exception as exc:
                llm = {f: "" for f in RESPONSE_FIELDS}
                llm["PMID"] = str(row["PMID"])
                llm["Vaccine Row"] = str(row["Vaccine Row"])
                llm["Final New Vaccine Status"] = "Uncertain - Needs Review"
                llm["AI Confidence"] = "Low"
                llm["Review Reason"] = f"Unhandled error: {exc}"

            key = normalize_name(row["Standardized Vaccine Name"])
            cluster_size = len(clusters.get(key, [row]))
            needs_check = compute_needs_human_check(row, llm, cluster_size)

            results.append(
                {
                    "PMID": row["PMID"],
                    "Vaccine Row": row["Vaccine Row"],
                    "Standardized Vaccine Name": row["Standardized Vaccine Name"],
                    "ROONEY NOTES": row.get("ROONEY NOTES", ""),
                    "Rooney Edited": row.get("Rooney Edited", ""),
                    "Cluster Size": cluster_size,
                    "Q1 Vaccine Purpose": llm.get("Q1 Vaccine Purpose", ""),
                    "Q2 Distinct Construct": llm.get("Q2 Distinct Construct", ""),
                    "Q3 Same As": llm.get("Q3 Same As", ""),
                    "Final New Vaccine Status": llm.get("Final New Vaccine Status", ""),
                    "Final Standardized Vaccine Name": llm.get("Final Standardized Vaccine Name", ""),
                    "AI Confidence": llm.get("AI Confidence", ""),
                    "Rooney Conflict": llm.get("Rooney Conflict", ""),
                    "Evidence Source For Q3": llm.get("Evidence Source For Q3", ""),
                    "Needs Human Check": needs_check,
                    "Review Reason": llm.get("Review Reason", ""),
                    "Matched VO Label": row.get("Matched VO Label", ""),
                    "VO Match Score": row.get("VO Match Score", ""),
                    "Matched VIOLIN Vaccine Name": row.get("Matched VIOLIN Vaccine Name", ""),
                    "VIOLIN Match Score": row.get("VIOLIN Match Score", ""),
                }
            )
            done += 1
            if done % 50 == 0 or done == total:
                print(f"Processed {done}/{total}", flush=True)

    results = normalize_duplicate_clusters(results)
    out_df = pd.DataFrame(results).sort_values(["PMID", "Vaccine Row"]).reset_index(drop=True)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(out_path, index=False)
    print(f"\nPreview report written to: {out_path.resolve()}")
    print("(new_brucella_vaccines_reviewed.xlsx and both source workbooks were NOT modified)")


if __name__ == "__main__":
    main()
