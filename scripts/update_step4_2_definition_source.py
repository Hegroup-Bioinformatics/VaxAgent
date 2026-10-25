#!/usr/bin/env python3
"""Update the Step 4.2 workbook with Definition source and vaccine role columns."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from openpyxl import load_workbook


def clean(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def normalize_pmid(value: object) -> str:
    text = clean(value)
    if not text:
        return ""
    if text.lower().startswith("pmid:"):
        return text
    return f"PMID:{text}"


def build_role_value(value: object) -> str:
    text = clean(value)
    if not text:
        return ""
    pieces = [part.strip() for part in text.split("|")]
    roles = []
    for part in pieces:
        part = re.sub(r"\s+", " ", part)
        if part:
            roles.append(f"{part} role")
    return " | ".join(roles)


def collect_duplicate_pmids(step4_1_path: Path) -> dict[str, list[str]]:
    workbook = load_workbook(step4_1_path, data_only=True)
    sheet = workbook[workbook.sheetnames[0]]
    headers = [clean(cell.value) for cell in sheet[1]]
    header_index = {name: idx for idx, name in enumerate(headers, start=1)}

    output: dict[str, list[str]] = {}
    for row_index in range(2, sheet.max_row + 1):
        duplicate_key = clean(sheet.cell(row=row_index, column=header_index.get("Duplicate record", 1)).value)
        pmid = clean(sheet.cell(row=row_index, column=header_index.get("PMID", 1)).value)
        if not duplicate_key or not pmid:
            continue
        output.setdefault(duplicate_key, [])
        pmid_value = normalize_pmid(pmid)
        if pmid_value not in output[duplicate_key]:
            output[duplicate_key].append(pmid_value)
    workbook.close()
    return output


def ensure_column(sheet, column_name: str, insert_after: str | None = None) -> int:
    headers = [clean(cell.value) for cell in sheet[1]]
    if column_name in headers:
        return headers.index(column_name) + 1

    if insert_after is None:
        sheet.cell(row=1, column=sheet.max_column + 1, value=column_name)
        return sheet.max_column

    insert_offset = headers.index(insert_after) + 1
    sheet.insert_cols(insert_offset + 1)
    sheet.cell(row=1, column=insert_offset + 1, value=column_name)
    return insert_offset + 1


def update_workbook(step4_1_path: Path, step4_2_path: Path) -> None:
    duplicate_pmids = collect_duplicate_pmids(step4_1_path)

    workbook = load_workbook(step4_2_path, data_only=False)
    sheet = workbook[workbook.sheetnames[0]]

    headers = [clean(cell.value) for cell in sheet[1]]
    header_map = {name: idx for idx, name in enumerate(headers, start=1)}

    if "Definition source" not in header_map:
        definition_col = ensure_column(sheet, "Definition source", "PMID")
    else:
        definition_col = header_map["Definition source"]

    headers = [clean(cell.value) for cell in sheet[1]]
    header_map = {name: idx for idx, name in enumerate(headers, start=1)}

    if "vaccine role" not in header_map:
        role_col = ensure_column(sheet, "vaccine role", "Vaccine Type (reviewed)")
    else:
        role_col = header_map["vaccine role"]

    headers = [clean(cell.value) for cell in sheet[1]]
    header_map = {name: idx for idx, name in enumerate(headers, start=1)}
    definition_col = header_map["Definition source"]
    role_col = header_map["vaccine role"]

    for row_index in range(2, sheet.max_row + 1):
        vaccine_num = clean(sheet.cell(row=row_index, column=header_map["Vaccine Num"]).value)
        pmid_value = clean(sheet.cell(row=row_index, column=header_map["PMID"]).value)
        own_pmid = normalize_pmid(pmid_value)

        values = []
        if own_pmid:
            values.append(own_pmid)
        for duplicate_pmid in duplicate_pmids.get(vaccine_num, []):
            if duplicate_pmid not in values:
                values.append(duplicate_pmid)

        sheet.cell(row=row_index, column=definition_col, value="|".join(values))

        vaccine_type = clean(sheet.cell(row=row_index, column=header_map["Vaccine Type (reviewed)"]).value)
        sheet.cell(row=row_index, column=role_col, value=build_role_value(vaccine_type))

    workbook.save(step4_2_path)
    workbook.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--step4_1",
        type=Path,
        default=Path("outputs/Step4.1_new_vaccines_VO.xlsx"),
        help="Path to Step4.1 workbook. Default: outputs/Step4.1_new_vaccines_VO.xlsx",
    )
    parser.add_argument(
        "--step4_2",
        type=Path,
        default=Path("outputs/Step4.2_new_vaccines_VO_only.xlsx"),
        help="Path to Step4.2 workbook. Default: outputs/Step4.2_new_vaccines_VO_only.xlsx",
    )
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    step4_1 = (project_root / args.step4_1).resolve() if not args.step4_1.is_absolute() else args.step4_1
    step4_2 = (project_root / args.step4_2).resolve() if not args.step4_2.is_absolute() else args.step4_2

    update_workbook(step4_1, step4_2)


if __name__ == "__main__":
    main()
