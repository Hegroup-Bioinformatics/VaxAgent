#!/usr/bin/env python3
"""Task 4 validation of Brucella vaccine candidates using the UMICH API."""

from __future__ import annotations

import argparse
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from openai import APIError, OpenAI, RateLimitError
from openpyxl import Workbook, load_workbook


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
ABSTRACTS_DIR = PROJECT_ROOT / "abstracts"
DEFAULT_INPUT_XLSX = (
    PROJECT_ROOT / "outputs" / "brucella_vaccine_mapping_propagated_vaccine_id_with_brucella_flag_review_vo_mapped.xlsx"
)
DEFAULT_OUTPUT_XLSX = (
    PROJECT_ROOT / "outputs" / "task4_vaccine_validation.xlsx"
)
PROMPT_FILE = PROJECT_ROOT / "prompts" / "vaccine-validation_prompt.txt"
ENV_FILE = PROJECT_ROOT / ".env"

TASK4_FIELDS = [
    "Task4 Novelty Status",
    "Task4 Primary Vaccine Paper",
    "Task4 Ready for VO/VIOLIN Addition",
    "Task4 Antigen Information",
    "Task4 Pathogen Information",
    "Task4 Vaccine Type Information",
    "Task4 Validation Confidence",
    "Task4 Validation Notes",
]


def clean_text(value: object) -> str:
    return str(value or "").strip()


def normalize_text(value: object) -> str:
    return re.sub(r"\s+", " ", clean_text(value).lower())


def load_system_prompt(prompt_file: Path) -> str:
    text = prompt_file.read_text(encoding="utf-8")
    match = re.search(r'SYSTEM_PROMPT\s*=\s*"""(.*?)"""', text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text.strip()


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


def parse_response(response_text: str) -> dict[str, str]:
    result = {field: "" for field in TASK4_FIELDS}
    label_pattern = re.compile(
        r"^(" + "|".join(re.escape(field) for field in TASK4_FIELDS) + r")\s*:\s*(.*)",
        re.IGNORECASE,
    )
    current_label: str | None = None
    current_lines: list[str] = []

    for line in response_text.splitlines():
        match = label_pattern.match(line)
        if match:
            if current_label is not None:
                result[current_label] = " ".join(current_lines).strip()
            raw_label = match.group(1)
            current_label = next(
                (field for field in TASK4_FIELDS if field.lower() == raw_label.lower()),
                raw_label,
            )
            current_lines = [match.group(2).strip()]
        elif current_label is not None:
            current_lines.append(line.strip())

    if current_label is not None:
        result[current_label] = " ".join(current_lines).strip()

    return result


def should_skip_row(row: dict[str, object]) -> bool:
    if normalize_text(row.get("Is Brucella Vaccine", "")) == "no":
        return True
    vaccine_id = clean_text(row.get("Vaccine ID", ""))
    if re.fullmatch(r"VO:\d+", vaccine_id):
        return True
    return False


def load_rows(input_xlsx: Path) -> tuple[list[str], list[dict[str, object]]]:
    workbook = load_workbook(input_xlsx, data_only=False)
    sheet = workbook.active
    headers = [clean_text(sheet.cell(row=1, column=index).value) for index in range(1, sheet.max_column + 1)]
    rows: list[dict[str, object]] = []
    for row_index in range(2, sheet.max_row + 1):
        item: dict[str, object] = {}
        for col_index, header in enumerate(headers, start=1):
            item[header] = sheet.cell(row=row_index, column=col_index).value
        item["__row_index__"] = row_index
        rows.append(item)
    return headers, rows


def read_abstract_text(pmid: str) -> str:
    if not pmid:
        return ""
    abstract_path = ABSTRACTS_DIR / f"{pmid}.txt"
    if not abstract_path.exists():
        return ""
    return abstract_path.read_text(encoding="utf-8", errors="ignore")


def build_user_payload(row: dict[str, object], headers: list[str], abstract_text: str) -> str:
    ordered_keys = [
        "PMID",
        "Title",
        "Standardized Vaccine Name",
        "Extracted Vaccine Name",
        "Proposed Vaccine Name",
        "Make Vaccine Name",
        "Pathogen",
        "Vaccine Type",
        "Gene or Protein Antigen",
        "Vaccine Formulation",
        "Adjuvant",
        "Target Vaccine Recipient or Host",
        "Lab Model or Cell",
        "Vaccine Development Stage",
        "In VO or VIOLIN",
        "Vaccine ID",
        "VO Vaccine Label",
        "Matched VIOLIN ID",
        "Matched VIOLIN Vaccine Name",
        "Matched VO ID",
        "Matched VO Label",
    ]
    lines = ["Structured record:"]
    used = set()
    for key in ordered_keys:
        if key in row:
            used.add(key)
            lines.append(f"{key}: {clean_text(row.get(key, ''))}")
    # Keep payload compact and focused; very long spreadsheet columns can suppress useful model output.
    extra_keys = [
        "Vaccine Efficacy",
        "Immune Response",
        "Vaccine Introduction",
        "Mapping Notes",
        "Conflict Review Note",
    ]
    for header in extra_keys:
        if header in row:
            value = clean_text(row.get(header, ""))
            if value:
                lines.append(f"{header}: {value}")
    lines.append("")
    lines.append("Publication abstract:")
    lines.append(abstract_text or "Abstract text not available.")
    return "\n".join(lines)


def process_row(
    row: dict[str, object],
    headers: list[str],
    client: OpenAI,
    model: str,
    system_prompt: str,
) -> tuple[int, dict[str, object]]:
    pmid = clean_text(row.get("PMID", ""))
    abstract_text = read_abstract_text(pmid)
    if not abstract_text:
        result = {field: "" for field in TASK4_FIELDS}
        result["Task4 Validation Confidence"] = "low"
        result["Task4 Validation Notes"] = "abstract text not available"
        return int(row["__row_index__"]), result

    payload = build_user_payload(row, headers, abstract_text)
    reply = ""
    for attempt in range(1, 4):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": payload},
                ],
                max_tokens=500,
            )
            reply = response.choices[0].message.content or ""
            break
        except RateLimitError:
            time.sleep(20 * attempt)
        except APIError as exc:
            if attempt == 3:
                reply = f"Task4 Validation Notes: API error after 3 attempts - {exc}"
            time.sleep(5)
    parsed = parse_response(reply)
    if not parsed.get("Task4 ValidationConfidence"):
        # no-op branch kept to avoid accidental key typos changing output shape
        pass
    if not parsed.get("Task4 Validation Confidence"):
        parsed["Task4 Validation Confidence"] = "low"
    if not parsed.get("Task4 Validation Notes"):
        parsed["Task4 Validation Notes"] = "response missing structured notes"
    return int(row["__row_index__"]), parsed


def write_output(
    path: Path,
    headers: list[str],
    rows: list[dict[str, object]],
    results: dict[int, dict[str, object]],
) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "task4_validation"
    out_headers = headers + TASK4_FIELDS
    sheet.append(out_headers)
    for row in rows:
        if should_skip_row(row):
            continue
        row_index = int(row["__row_index__"])
        output_values = [row.get(header, "") for header in headers]
        task4 = results.get(row_index, {field: "" for field in TASK4_FIELDS})
        output_values.extend(task4.get(field, "") for field in TASK4_FIELDS)
        sheet.append(output_values)
    sheet.freeze_panes = "A2"
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Task 4 validation of vaccine candidates with UMICH API.")
    parser.add_argument("--input-xlsx", default=str(DEFAULT_INPUT_XLSX))
    parser.add_argument("--out-xlsx", default=str(DEFAULT_OUTPUT_XLSX))
    parser.add_argument("--prompt-file", default=str(PROMPT_FILE))
    parser.add_argument("--model", default="claude-sonnet-5")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit", type=int, default=0, help="Only process first N eligible rows (0 = all)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not load_env_file(ENV_FILE):
        print(f"ERROR: Could not load .env from {ENV_FILE}", file=sys.stderr)
        sys.exit(1)
    api_key = os.environ.get("OPENAI_API_KEY")
    api_base = os.environ.get("OPENAI_API_BASE")
    if not api_key or not api_base:
        print("ERROR: OPENAI_API_KEY and OPENAI_API_BASE must be set in .env", file=sys.stderr)
        sys.exit(1)

    input_xlsx = Path(args.input_xlsx)
    out_xlsx = Path(args.out_xlsx)
    prompt_file = Path(args.prompt_file)
    system_prompt = load_system_prompt(prompt_file)
    headers, rows = load_rows(input_xlsx)
    eligible_rows = [row for row in rows if not should_skip_row(row)]
    if args.limit > 0:
        eligible_rows = eligible_rows[: args.limit]

    thread_local = threading.local()

    def get_client() -> OpenAI:
        if not hasattr(thread_local, "client"):
            thread_local.client = OpenAI(api_key=api_key, base_url=api_base)
        return thread_local.client

    results: dict[int, dict[str, object]] = {}
    total = len(eligible_rows)
    print(f"Eligible rows for Task 4 validation: {total}")

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        future_map = {
            executor.submit(process_row, row, headers, get_client(), args.model, system_prompt): row
            for row in eligible_rows
        }
        done = 0
        for future in as_completed(future_map):
            row = future_map[future]
            try:
                row_index, parsed = future.result()
            except Exception as exc:
                row_index = int(row["__row_index__"])
                parsed = {field: "" for field in TASK4_FIELDS}
                parsed["Task4 Validation Confidence"] = "low"
                parsed["Task4 Validation Notes"] = f"Unhandled error: {exc}"
            results[row_index] = parsed
            done += 1
            if done % 50 == 0 or done == total:
                print(f"Processed {done}/{total} eligible rows", flush=True)

    write_output(out_xlsx, headers, rows if args.limit == 0 else eligible_rows, results)
    print(f"Wrote: {out_xlsx.resolve()}")


if __name__ == "__main__":
    main()