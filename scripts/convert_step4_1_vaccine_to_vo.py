#!/usr/bin/env python3
"""Convert Step 3.2 reviewed Brucella vaccines into the VO template.

Rules implemented:
- Keep only rows with non-blank Vaccine Num.
- Exclude rows whose Duplicate record is populated.
- For each kept row, aggregate definition source with the row's own PMID plus all
  PMIDs from rows whose Duplicate record matches that Vaccine Num.
- Save the final result as an Excel workbook in outputs/Step4.1_new_Brucella_vaccines_VO.xlsx.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'outputs' / 'Step3.2_new_Brucella_vaccines_reviewed.xlsx'
TEMPLATE = ROOT / 'inputs' / 'vaccine_Brucella.csv'
OUT = ROOT / 'outputs' / 'Step4.1_new_Brucella_vaccines_VO.xlsx'


def clean(value):
    if pd.isna(value):
        return ''
    s = str(value).strip()
    if s.lower() in {'nan', 'none', 'not reported', 'not report', 'n/a'}:
        return ''
    return s


def normalize_num(value):
    s = clean(value)
    if not s:
        return ''
    try:
        num = float(s)
    except ValueError:
        return s
    if num.is_integer():
        return str(int(num))
    return str(num)


def split_multi(value):
    s = clean(value)
    if not s:
        return []
    parts = re.split(r'[;|]', s)
    return [p.strip() for p in parts if p.strip()]


def pick_pathogen(pathogen_value):
    parts = split_multi(pathogen_value)
    if not parts:
        return ''
    brucella = [p for p in parts if p.lower().startswith('brucella')]
    if brucella:
        return brucella[0]
    return parts[0]


def pick_vaccine_type(vtype_value):
    types = split_multi(vtype_value)
    if not types:
        return ''
    return types[0]


def parse_lab_animal(value):
    s = clean(value)
    if not s:
        return ''
    s = s.split('|')[0].split(';')[0].strip()
    s = re.sub(r'\s*\(.*?\)\s*$', '', s)
    return s


def disease_from_pathogen(pathogen_value):
    p = clean(pathogen_value)
    if not p:
        return ''
    if 'Brucella' in p:
        return 'brucellosis'
    if 'Bacillus anthracis' in p:
        return 'anthrax'
    return p.lower()


def parent_name(row):
    if clean(row.get('Combination Vaccine')).lower() == 'yes':
        return 'combination vaccine'

    pathogen = pick_pathogen(row.get('Pathogen (reviewed)'))
    vtype = pick_vaccine_type(row.get('Vaccine Type (reviewed)'))

    if not pathogen and not vtype:
        return ''
    if not pathogen:
        return vtype
    if not vtype:
        return pathogen

    lt = vtype.lower()
    if lt in {'live attenuated vaccine', 'live virulent vaccine', 'inactivated vaccine'}:
        return f'{pathogen} {lt}'
    return f'{pathogen} {vtype}'


def definition_source_for_row(row, df):
    pmids = []
    seen = set()

    current_num = normalize_num(row.get('Vaccine Num'))
    # Own PMID
    own_pmid = clean(row.get('PMID'))
    if own_pmid:
        own_value = f'PMID:{own_pmid}'
        pmids.append(own_value)
        seen.add(own_value)

    # Appended PMIDs from duplicate records mapped back to this master vaccine num
    if current_num:
        dup_rows = df[df['Duplicate record'].map(normalize_num) == current_num]
        for _, dup_row in dup_rows.iterrows():
            dup_pmid = clean(dup_row.get('PMID'))
            if not dup_pmid:
                continue
            val = f'PMID:{dup_pmid}'
            if val not in seen:
                pmids.append(val)
                seen.add(val)

    return '|'.join(pmids)


def build_row(row, template_columns, df):
    result = {column: '' for column in template_columns}

    label = clean(row.get('Vaccine Name (final)'))
    viol_label = clean(row.get('Vaccine Name in VIOLIN'))
    result['LABEL'] = label
    result['VIOLIN label'] = viol_label
    if label and viol_label and label != viol_label:
        result['alternative label'] = viol_label

    vtypes = []
    for val in split_multi(row.get('Vaccine Type (reviewed)')):
        vtypes.append(f'{val} role')
    if clean(row.get('Combination Vaccine')).lower() == 'yes':
        vtypes.append('combination vaccine role')
    result['vaccine type'] = '|'.join(vtypes)

    result['definition source'] = definition_source_for_row(row, df)
    result['immunizes against pathogen'] = clean(row.get('Pathogen (reviewed)'))
    result['immunizes recipient'] = clean(row.get('Target Vaccine Recipient or Host (reviewed)'))
    result['lab animal'] = parse_lab_animal(row.get('Lab Model or Cell (reviewed)'))
    result['status of vaccine development'] = clean(row.get('Vaccine Development Stage (reviewed)'))
    result['term editor'] = 'VaxAgent | Jie Zheng'
    result['inSubset'] = 'vaccine'
    result['Category'] = 'VIOLIN'
    result['immunizes against disease'] = disease_from_pathogen(row.get('Pathogen (reviewed)'))
    result['Parent (name)'] = parent_name(row)

    return result


def main():
    full_df = pd.read_excel(SRC)

    # Keep only rows with non-blank Vaccine Num in the full source table.
    full_df = full_df[full_df['Vaccine Num'].notna()].copy()
    full_df['Vaccine Num'] = full_df['Vaccine Num'].map(normalize_num)

    # Export only rows whose Duplicate record is blank, but compute definition source from the full source table.
    master_df = full_df[full_df['Duplicate record'].isna() | (full_df['Duplicate record'].astype(str).str.strip() == '')].copy()

    # Sort ascending by Vaccine Num
    master_df['Vaccine Num_num'] = pd.to_numeric(master_df['Vaccine Num'], errors='coerce')
    master_df = master_df.sort_values('Vaccine Num_num', na_position='last').reset_index(drop=True)

    template_columns = pd.read_csv(TEMPLATE, nrows=0).columns.tolist()
    rows = [build_row(row, template_columns, full_df) for _, row in master_df.iterrows()]
    output_df = pd.DataFrame(rows, columns=template_columns)

    for col in output_df.columns:
        output_df[col] = output_df[col].fillna('')

    output_df.to_excel(OUT, index=False)
    print(f'rows_written={len(output_df)}')
    print(f'output={OUT}')


if __name__ == '__main__':
    main()
