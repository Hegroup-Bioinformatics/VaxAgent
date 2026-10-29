#!/usr/bin/env python3
"""Extract structured vaccine data from abstract txt files using the UMICH LLM API.

Runs with a configurable thread pool (default 6) for parallel API calls.
"""

from __future__ import annotations

import csv
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI, RateLimitError, APIError

# ---------------------------------------------------------------------------
# Paths (all relative to the project root, which is one level above scripts/)
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
ABSTRACTS_DIR = PROJECT_ROOT / "abstracts"
OUTPUT_CSV = PROJECT_ROOT / "vaccine-data" / "llm_vaccine_extraction_results.csv"
PROMPT_FILE = PROJECT_ROOT / "prompts" / "vaccine-extraction_prompt.txt"
ENV_FILE = PROJECT_ROOT / ".env"

# ---------------------------------------------------------------------------
# Output fields (must match the labels the LLM is asked to produce)
# ---------------------------------------------------------------------------
FIELD_LABELS = [
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
]

CSV_FIELDNAMES = ["source_file", "skip_reason"] + FIELD_LABELS


# ---------------------------------------------------------------------------
# Load the system prompt from the prompt file
# ---------------------------------------------------------------------------
def load_system_prompt() -> str:
    text = PROMPT_FILE.read_text(encoding="utf-8")
    # The prompt file defines SYSTEM_PROMPT = """..."""; extract just that block.
    match = re.search(r'SYSTEM_PROMPT\s*=\s*"""(.*?)"""', text, re.DOTALL)
    if match:
        return match.group(1).strip()
    # Fallback: strip comment lines and use everything before the field definitions
    lines = []
    for line in text.splitlines():
        if line.startswith("#"):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# Abstract file parsing
# ---------------------------------------------------------------------------
def read_abstract_file(file_path: Path) -> str:
    """Return the full text of a single abstract file."""
    return file_path.read_text(encoding="utf-8", errors="ignore")


# ---------------------------------------------------------------------------
# LLM response parsing
# ---------------------------------------------------------------------------
def parse_llm_response(response_text: str) -> dict[str, str]:
    """Parse the labeled-field response into a dictionary."""
    result: dict[str, str] = {f: "" for f in FIELD_LABELS}
    result["skip_reason"] = ""

    # Check for SKIP signal
    skip_match = re.search(r"SKIP\s*:\s*(.+)", response_text, re.IGNORECASE)
    if skip_match:
        result["skip_reason"] = skip_match.group(1).strip()
        return result

    # Split into lines and collect multi-line values
    current_label: str | None = None
    current_value_lines: list[str] = []

    # Build a regex that matches any known label at the start of a line
    label_pattern = re.compile(
        r"^(" + "|".join(re.escape(lbl) for lbl in FIELD_LABELS) + r")\s*:\s*(.*)",
        re.IGNORECASE,
    )

    for line in response_text.splitlines():
        m = label_pattern.match(line)
        if m:
            # Save the previous field
            if current_label is not None:
                result[current_label] = " ".join(current_value_lines).strip()
            # Normalise label to canonical casing
            raw_label = m.group(1)
            current_label = next(
                (lbl for lbl in FIELD_LABELS if lbl.lower() == raw_label.lower()),
                raw_label,
            )
            current_value_lines = [m.group(2).strip()]
        elif current_label is not None:
            current_value_lines.append(line.strip())

    if current_label is not None:
        result[current_label] = " ".join(current_value_lines).strip()

    return result


# ---------------------------------------------------------------------------
# Already-processed PMID set (for resuming)
# ---------------------------------------------------------------------------
def load_processed_pmids(csv_path: Path) -> set[str]:
    if not csv_path.exists():
        return set()
    processed: set[str] = set()
    with csv_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("source_file"):
                processed.add(row["source_file"])
    return processed


# ---------------------------------------------------------------------------
# Per-file worker (called from thread pool)
# ---------------------------------------------------------------------------
def process_file(
    file_path: Path,
    client: OpenAI,
    model: str,
    system_prompt: str,
) -> dict[str, str]:
    """Call the LLM for one abstract file and return the parsed result."""
    abstract_text = read_abstract_file(file_path)

    reply = ""
    for attempt in range(1, 4):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": abstract_text},
                ],
                temperature=0,
            )
            reply = response.choices[0].message.content or ""
            break
        except RateLimitError:
            wait = 30 * attempt
            time.sleep(wait)
        except APIError as exc:
            if attempt == 3:
                reply = f"SKIP: API error after 3 attempts — {exc}"
            time.sleep(5)
    else:
        reply = "SKIP: API error after 3 attempts"

    parsed = parse_llm_response(reply)
    parsed["source_file"] = file_path.name
    return parsed


# ---------------------------------------------------------------------------
# Main extraction loop (parallel)
# ---------------------------------------------------------------------------
def main(workers: int = 6) -> None:
    # Load credentials from project-root .env
    if not load_dotenv(ENV_FILE):
        print(f"ERROR: Could not load .env from {ENV_FILE}", file=sys.stderr)
        sys.exit(1)

    api_key = os.environ.get("OPENAI_API_KEY")
    api_base = os.environ.get("OPENAI_API_BASE")
    model = os.environ.get("MODEL", "gpt-4o-mini")

    if not api_key or not api_base:
        print("ERROR: OPENAI_API_KEY and OPENAI_API_BASE must be set in .env", file=sys.stderr)
        sys.exit(1)

    # Each thread gets its own client to avoid sharing state
    def make_client() -> OpenAI:
        return OpenAI(api_key=api_key, base_url=api_base)

    system_prompt = load_system_prompt()

    txt_files = sorted(ABSTRACTS_DIR.glob("*.txt"))
    print(f"Found {len(txt_files)} abstract files in {ABSTRACTS_DIR}")

    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)

    # Resume support: detect already-processed files
    processed = load_processed_pmids(OUTPUT_CSV)
    pending = [f for f in txt_files if f.name not in processed]
    print(f"Already processed: {len(processed)}  |  Remaining: {len(pending)}")
    print(f"Running with {workers} parallel workers")

    write_header = not OUTPUT_CSV.exists() or len(processed) == 0
    mode = "w" if write_header else "a"

    csv_lock = threading.Lock()
    counter = {"done": 0, "ok": 0, "skipped": 0}
    total_pending = len(pending)

    with OUTPUT_CSV.open(mode, newline="", encoding="utf-8") as out_f:
        writer = csv.DictWriter(out_f, fieldnames=CSV_FIELDNAMES, extrasaction="ignore")
        if write_header:
            writer.writeheader()

        # Thread-local clients — one per thread
        thread_local = threading.local()

        def get_client() -> OpenAI:
            if not hasattr(thread_local, "client"):
                thread_local.client = make_client()
            return thread_local.client

        def worker(file_path: Path) -> dict[str, str]:
            return process_file(file_path, get_client(), model, system_prompt)

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(worker, fp): fp for fp in pending}

            for future in as_completed(futures):
                fp = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = {f: "" for f in FIELD_LABELS}
                    result["source_file"] = fp.name
                    result["skip_reason"] = f"Unhandled error: {exc}"

                status = "SKIPPED" if result.get("skip_reason") else "OK"

                with csv_lock:
                    writer.writerow(result)
                    out_f.flush()
                    counter["done"] += 1
                    if status == "OK":
                        counter["ok"] += 1
                    else:
                        counter["skipped"] += 1
                    n = counter["done"]

                print(f"[{n}/{total_pending}] {fp.name} ... {status}", flush=True)

    total = len(processed) + total_pending
    print(f"\nDone. Results written to: {OUTPUT_CSV.resolve()}")
    print(f"Total records: {total}  |  OK: {counter['ok']}  |  Skipped: {counter['skipped']}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=6, help="Number of parallel API workers")
    args = parser.parse_args()
    main(workers=args.workers)
