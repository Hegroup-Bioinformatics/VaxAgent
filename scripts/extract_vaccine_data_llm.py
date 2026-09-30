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
from openai import APIError, AzureOpenAI, OpenAI, RateLimitError

# ---------------------------------------------------------------------------
# Paths (all relative to the project root, which is one level above scripts/)
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
ABSTRACTS_DIR = PROJECT_ROOT / "abstracts"
OUTPUT_CSV = PROJECT_ROOT / "vaccine-data" / "llm_vaccine_extraction_results.csv"
PROMPT_FILE = PROJECT_ROOT / "prompts" / "task2" / "abstract-extraction.txt"
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
def process_abstract(
    source_file: str,
    abstract_text: str,
    client: OpenAI,
    model: str,
    system_prompt: str,
) -> dict[str, str]:
    """Call the LLM for one abstract record and return the parsed result."""
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
        except RateLimitError as exc:
            if attempt == 3:
                reply = f"SKIP: API rate limit after 3 attempts - {exc}"
                break
            wait = 30 * attempt
            time.sleep(wait)
        except APIError as exc:
            if attempt == 3:
                reply = f"SKIP: API error after 3 attempts - {exc}"
                break
            time.sleep(5)
    else:
        reply = "SKIP: API error after 3 attempts"

    parsed = parse_llm_response(reply)
    parsed["source_file"] = source_file
    return parsed


def process_file(
    file_path: Path,
    client: OpenAI,
    model: str,
    system_prompt: str,
) -> dict[str, str]:
    """Call the LLM for one standalone abstract file."""
    return process_abstract(
        file_path.name,
        read_abstract_file(file_path),
        client,
        model,
        system_prompt,
    )


def load_collection_records(file_path: Path) -> list[tuple[str, str]]:
    """Split the legacy collection TXT into (source_file, record) pairs."""
    content = file_path.read_text(encoding="utf-8", errors="ignore")
    records = [record.strip() for record in content.split("\n\n\n") if record.strip()]
    parsed_records: list[tuple[str, str]] = []
    for index, record in enumerate(records, start=1):
        match = re.search(r"^PMID:\s*(\d+)", record, re.MULTILINE | re.IGNORECASE)
        source_file = f"{match.group(1)}.txt" if match else f"collection_record_{index}.txt"
        parsed_records.append((source_file, record))
    return parsed_records


# ---------------------------------------------------------------------------
# Main extraction loop (parallel)
# ---------------------------------------------------------------------------
def main(
    workers: int = 6,
    input_dir: Path = ABSTRACTS_DIR,
    input_file: Path | None = None,
    output_csv: Path = OUTPUT_CSV,
    limit: int | None = None,
) -> None:
    # Load credentials from project-root .env
    if not load_dotenv(ENV_FILE):
        print(f"ERROR: Could not load .env from {ENV_FILE}", file=sys.stderr)
        sys.exit(1)

    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("API_KEY")
    api_base = os.environ.get("OPENAI_API_BASE")
    azure_endpoint = os.environ.get("ENDPOINT")
    azure_api_version = os.environ.get("API_VERSION")
    model = os.environ.get("MODEL") or os.environ.get("DEPLOYMENT", "gpt-5")

    if not api_key:
        print("ERROR: OPENAI_API_KEY or API_KEY must be set in .env", file=sys.stderr)
        sys.exit(1)

    if azure_endpoint and azure_api_version and os.environ.get("DEPLOYMENT"):
        client_factory = lambda: AzureOpenAI(
            api_key=api_key,
            azure_endpoint=azure_endpoint,
            api_version=azure_api_version,
        )
        print(f"Using Azure UMGPT deployment: {model}")
    elif api_base:
        client_factory = lambda: OpenAI(api_key=api_key, base_url=api_base)
        print(f"Using OpenAI-compatible UMGPT endpoint: {api_base}")
    else:
        print(
            "ERROR: set OPENAI_API_BASE for the OpenAI-compatible API or "
            "ENDPOINT, API_VERSION, and DEPLOYMENT for Azure UMGPT",
            file=sys.stderr,
        )
        sys.exit(1)

    # Each thread gets its own client to avoid sharing state
    def make_client() -> OpenAI | AzureOpenAI:
        return client_factory()

    system_prompt = load_system_prompt()

    if input_file:
        records = load_collection_records(input_file)
        items: list[Path | tuple[str, str]] = records
        print(f"Found {len(records)} abstract records in {input_file}")
    else:
        txt_files = sorted(input_dir.glob("*.txt"))
        items = txt_files
        print(f"Found {len(txt_files)} abstract files in {input_dir}")

    if limit is not None:
        items = items[:limit]
        print(f"Limiting run to {len(items)} records")

    output_csv.parent.mkdir(parents=True, exist_ok=True)

    # Resume support: detect already-processed files
    processed = load_processed_pmids(output_csv)
    pending = [item for item in items if (item[0] if isinstance(item, tuple) else item.name) not in processed]
    print(f"Already processed: {len(processed)}  |  Remaining: {len(pending)}")
    print(f"Running with {workers} parallel workers")

    write_header = not output_csv.exists() or len(processed) == 0
    mode = "w" if write_header else "a"

    csv_lock = threading.Lock()
    counter = {"done": 0, "ok": 0, "skipped": 0}
    total_pending = len(pending)

    with output_csv.open(mode, newline="", encoding="utf-8") as out_f:
        writer = csv.DictWriter(out_f, fieldnames=CSV_FIELDNAMES, extrasaction="ignore")
        if write_header:
            writer.writeheader()

        # Thread-local clients — one per thread
        thread_local = threading.local()

        def get_client() -> OpenAI:
            if not hasattr(thread_local, "client"):
                thread_local.client = make_client()
            return thread_local.client

        def worker(item: Path | tuple[str, str]) -> dict[str, str]:
            if isinstance(item, tuple):
                return process_abstract(item[0], item[1], get_client(), model, system_prompt)
            return process_file(item, get_client(), model, system_prompt)

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(worker, item): item for item in pending}

            for future in as_completed(futures):
                item = futures[future]
                source_file = item[0] if isinstance(item, tuple) else item.name
                try:
                    result = future.result()
                except Exception as exc:
                    result = {f: "" for f in FIELD_LABELS}
                    result["source_file"] = source_file
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

                print(f"[{n}/{total_pending}] {source_file} ... {status}", flush=True)

    total = len(processed) + total_pending
    print(f"\nDone. Results written to: {output_csv.resolve()}")
    print(f"Total records: {total}  |  OK: {counter['ok']}  |  Skipped: {counter['skipped']}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=6, help="Number of parallel API workers")
    parser.add_argument("--input-dir", type=Path, default=ABSTRACTS_DIR)
    parser.add_argument("--input-file", type=Path, help="Legacy collection TXT with records separated by blank lines")
    parser.add_argument("--output-csv", type=Path, default=OUTPUT_CSV)
    parser.add_argument("--limit", type=int, help="Process only the first N records")
    args = parser.parse_args()
    main(
        workers=args.workers,
        input_dir=args.input_dir,
        input_file=args.input_file,
        output_csv=args.output_csv,
        limit=args.limit,
    )
