#!/usr/bin/env python3
"""Build Step 4.2 text-definition workbook from the reviewed Step 4.1 Brucella vaccine sheet.

The workbook keeps all original columns and appends a new column,
Proposed_VO_Definition, generated as:
    [Parent (name)] that [Definition]

The text is polished into a concise sentence and, when available, includes the
adjuvant and the animal or cell model used in the study.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / 'outputs' / 'Step4.1_new_Brucella_vaccines_VO_reviewed.xlsx'
OUTPUT = ROOT / 'outputs' / 'Step4.2_new_Brucella_vaccines_template.xlsx'


def clean(value):
    if pd.isna(value):
        return ''
    s = str(value).strip()
    if s.lower() in {'nan', 'none', 'not reported', 'not report', 'n/a', 'not specified', 'not specified.'}:
        return ''
    return s


def sentence_case_first(text: str) -> str:
    text = clean(text)
    if not text:
        return ''
    text = re.sub(r'\s+', ' ', text).strip()
    text = text.split(';', 1)[0].strip()
    text = text.rstrip('. ')
    if not text:
        return ''

    tokens = text.split()
    normalized = []
    keep_caps = {
        'brucella', 'abortus', 'melitensis', 'ovis', 'canis', 'suis', 'rb51', 's19',
        'btuB', 'lptd', 'mlta', 'prpa', 'virj', 'atpd', 'dnak', 'f8', 'pi', 'sod',
        'cfu', 'lps', 'co2', 'omv', 'omvs', 'diva', 'vjbR', 'per', 'bbl', 'bsa',
        'rev', 'rev.', 'iv', 'ii', 'iii', 'iv.'
    }
    for i, token in enumerate(tokens):
        low = token.lower()
        if i == 0:
            if token and token[0].isalpha():
                normalized.append(token[:1].upper() + token[1:].lower())
            else:
                normalized.append(token)
        elif re.fullmatch(r'[A-Z0-9]+(?:[./-][A-Z0-9]+)*', token) or low in keep_caps:
            normalized.append(token)
        elif re.fullmatch(r'\d+[A-Z]', token):
            normalized.append(token)
        else:
            normalized.append(token.lower())

    text = ' '.join(normalized)
    if text and text[-1] != '.':
        text += '.'
    return text


def clean_definition_sentence(s: str) -> str:
    s = clean(s)
    if not s:
        return ''
    s = s.replace('\n', ' ')
    s = re.sub(r'\s+', ' ', s).strip()
    s = s.split(';', 1)[0].strip()
    s = re.sub(r'\s+([.,;:!?])', r'\1', s)
    s = s.rstrip('. ')
    if not s:
        return ''
    if re.search(r'\b(?:not reported|in research|adjuvant|research)\b', s, flags=re.I):
        return ''
    return sentence_case_first(s)


def finalize_definition_text(s: str) -> str:
    s = clean(s)
    if not s:
        return ''
    s = s.replace('\n', ' ')
    s = re.sub(r'\s+', ' ', s)
    s = re.sub(r'(?i)\bthat is in research\b', '', s)
    s = re.sub(r'(?i)\bthat is\s+in research\b', '', s)
    s = re.sub(r'(?i)\b(?:in research|adjuvant)\b', '', s)
    s = re.sub(r'\s+', ' ', s)
    s = s.replace(' .', '.')
    s = s.strip(' .')
    if not s:
        return ''
    s = re.sub(r'(?i)^\s*that is\s+', '', s)
    s = s.replace('..', '.')

    parts = [p.strip() for p in re.split(r'(?<=[.!?])\s+', s) if p.strip()]
    if not parts:
        return ''
    out = []
    for part in parts:
        part = sentence_case_first(part)
        if part:
            out.append(part)
    result = ' '.join(out)
    if result and not result.endswith('.'):
        result += '.'
    return result


def extract_section(defn: str, heading: str) -> str:
    defn = clean(defn)
    if not defn:
        return ''
    defn = defn.split('===VACCINE===', 1)[0]
    pattern = rf'{re.escape(heading)}\s*:\s*(.*?)(?=(?:\n|\r\n)?(?:Vaccine Formulation|Vaccine Efficacy|Immune Response|Experiment Used|Vaccine Introduction|\Z))'
    match = re.search(pattern, defn, flags=re.I | re.S)
    if not match:
        return ''
    section = match.group(1).strip()
    section = section.replace('\n', ' ')
    section = re.sub(r'\s+', ' ', section)
    section = section.strip(' -;:,. ')
    if section.lower() in {'not reported', 'adjuvant', 'in research'}:
        return ''
    return section


def normalize_definition_text(defn: str) -> str:
    defn = clean(defn)
    if not defn:
        return ''
    defn = defn.rstrip('. ')
    lower = defn.lower()

    for prefix in ['Experimental ', 'experimental ', 'The ', 'the ', 'This ', 'this ', 'A ', 'a ', 'An ', 'an ']:
        if lower.startswith(prefix.lower()):
            defn = defn[len(prefix):]
            lower = defn.lower()
            break

    if lower.startswith('is '):
        pass
    elif lower.startswith('contains '):
        defn = 'is ' + defn
    elif lower.startswith('composed '):
        defn = 'is a ' + defn
    elif lower.startswith('uses '):
        defn = 'is ' + defn
    elif lower.startswith('made '):
        defn = 'is ' + defn
    elif lower.startswith('prepared '):
        defn = 'is ' + defn
    elif lower.startswith('consists '):
        defn = 'is ' + defn
    elif lower.startswith('extracted '):
        defn = 'is a ' + defn
    elif not lower.startswith(('is ', 'contains ', 'uses ', 'made ', 'prepared ', 'consists ', 'includes ', 'contains a ', 'contains the ', 'is a ', 'is an ')):
        if lower.startswith('study '):
            defn = 'is a study in which ' + defn[6:]
        elif lower.startswith('studies '):
            defn = 'is a study in which ' + defn[8:]
        elif lower.startswith('two '):
            defn = 'is a formulation in which ' + defn[4:]
        elif lower.startswith(('vaccine ', 'cell ', 'fraction ', 'extract ', 'protein ', 'peptide ', 'antigen ', 'mixture ', 'ribosome', 'lipopolysaccharide')):
            defn = 'is a ' + defn
        else:
            defn = 'is ' + defn

    if defn and defn[0].isalpha() and defn[0].isupper():
        defn = defn[0].lower() + defn[1:]

    defn = re.sub(r'^is a study in which examined\b', 'is a study that examined', defn, flags=re.I)
    defn = re.sub(r'^is a study in which evaluated\b', 'is a study that evaluated', defn, flags=re.I)
    defn = re.sub(r'^is a study in which investigated\b', 'is a study that investigated', defn, flags=re.I)
    defn = re.sub(r'^is a study in which aimed\b', 'is a study that aimed', defn, flags=re.I)
    defn = re.sub(r'^is a study in which designed\b', 'is a study that designed', defn, flags=re.I)
    defn = re.sub(r'^is a study in which identified\b', 'is a study that identified', defn, flags=re.I)
    defn = re.sub(r'^is a genetic mutant derived from\b', 'is a genetic mutant derived from', defn, flags=re.I)
    return defn.rstrip('. ')


def extract_label_antigen(label: str) -> str:
    label = clean(label)
    if not label:
        return ''
    cleaned = label
    for prefix in [
        'live attenuated ', 'inactivated ', 'attenuated ', 'recombinant ', 'subunit ', 'fusion ',
        'DNA ', 'mRNA ', 'RNA ', 'vector ', 'conjugate ', 'whole-cell ', 'whole cell ',
        'live ', 'killed ', 'adjuvanted ', 'with '
    ]:
        cleaned = re.sub(rf'^{re.escape(prefix)}', '', cleaned, flags=re.I)
    cleaned = re.sub(r'^(Brucella abortus|Brucella melitensis|Brucella ovis|Brucella canis|Brucella)\s+', '', cleaned, flags=re.I)
    cleaned = re.sub(r'\s+(vaccine|vaccines)$', '', cleaned, flags=re.I)
    cleaned = re.sub(r'\s+with\s+.*$', '', cleaned, flags=re.I)
    cleaned = re.sub(r'\s+adjuvant.*$', '', cleaned, flags=re.I)
    cleaned = cleaned.strip(' ,;-')
    patterns = [
        'cell wall fraction', 'fraction', 'protein', 'antigen', 'lipopolysaccharide', 'extract',
        'peptide', 'fusion protein', 'recombinant protein', 'omp', 'l7/l12', 'sod', 'ag85a',
        'bp26', 'omp31', 'omp25', 'omp2b', 'btub', 'lptd', 'p39', 'groel'
    ]
    if any(p in cleaned.lower() for p in patterns):
        return cleaned
    return ''


def extract_unique_label_phrase(label: str) -> str:
    label = clean(label)
    if not label:
        return ''

    lower = label.lower()
    generic_tokens = {
        'brucella', 'live', 'attenuated', 'inactivated', 'recombinant', 'subunit', 'vaccine', 'vaccines',
        'and', 'with', 'double', 'single', 'deletion', 'mutant', 'strain', 'fusion', 'protein',
        'antigen', 'dna', 'rna', 'mrna', 'vector', 'conjugate', 'whole', 'cell', 'fraction', 'extract',
        'study', 'using', 'expressing', 'encoding', 'immunogenic', 'gene', 'genes', 'or'
    }

    for pat in [
        r'\b(?:[A-Z][a-z0-9]+\s+and\s+[A-Z][a-z0-9]+)',
        r'\b(?:strain\s+[A-Za-z0-9/.-]+(?:\s+[A-Za-z0-9/.-]+){0,4})',
        r'\b(?:cydC\s+purD|cydC\s+cydD|cydD\s+purD|purD\s+double\s+deletion|cydC\s+double\s+deletion|wbkF|wadB|wadC|groEL|p39|bp26|omp31|omp25|omp2b|btuB|lptD|l7/l12|esat-6|mpt70|mpt83)\b',
        r'\b(?:BtuB|LptD|MltA|PrpA|VirJ|AtpD|DnaK|BAbS19_II02030|RB51|CydC|PurD)\b',
        r'\b(?:[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)*)\b'
    ]:
        m = re.search(pat, label, flags=re.I)
        if m:
            text = m.group(0).strip()
            if text.lower() in generic_tokens:
                continue
            # strip generic trailing qualifiers from a strain phrase
            pieces = [p for p in re.split(r'\s+', text) if p.lower() not in generic_tokens and p.lower() not in {'deletion', 'double', 'single', 'mutant'}]
            if pieces:
                return ' '.join(pieces[:4])

    if 'with' in lower and 'adjuvant' in lower:
        m = re.search(r'with\s+([^,;]+?)\s+adjuvant', lower, flags=re.I)
        if m:
            return f'with {m.group(1).strip()} adjuvant'
    return ''


def label_specific_definition_smoothing(label: str, normalized: str) -> str:
    label = clean(label)
    if not label:
        return normalized

    lower = label.lower()

    if 'rb51' in lower and ('deletion' in lower or 'cydc' in lower or 'purd' in lower or 'cydd' in lower):
        mutant_bits = []
        for token in ['cydC', 'cydD', 'purD', 'double deletion', 'deletion']:
            if token.lower() in lower:
                mutant_bits.append(token)
        if mutant_bits:
            return 'is a live attenuated RB51-derived vaccine carrying the ' + ', '.join(mutant_bits) + ' mutation'
        return 'is a live attenuated RB51-derived vaccine with targeted deletion mutations'

    for gene in ['MltA', 'PrpA', 'VirJ', 'AtpD', 'DnaK', 'BAbS19_II02030', 'BtuB', 'LptD']:
        if gene.lower() in lower:
            if gene in {'MltA', 'PrpA', 'VirJ', 'AtpD', 'DnaK', 'BAbS19_II02030'}:
                return f'is a subunit vaccine targeting {gene}'
            if gene in {'BtuB', 'LptD'}:
                return 'is an RNA vaccine targeting BtuB and LptD'

    if 'btuB' in lower and 'lptd' in lower:
        return 'is an RNA vaccine targeting BtuB and LptD'

    return normalized


def extract_parent_difference(row):
    label = clean(row.get('LABEL'))
    definition = clean(row.get('definition'))
    label_lower = label.lower()

    if not label:
        return 'the specific formulation described in this entry'

    # Prefer the distinct label phrase over generic parent description.
    cleaned = label
    for species in [
        'Brucella abortus', 'Brucella melitensis', 'Brucella ovis', 'Brucella canis',
        'Brucella suis', 'Brucella'
    ]:
        cleaned = re.sub(rf'^{re.escape(species)}\s+', '', cleaned, flags=re.I)
    cleaned = re.sub(r'^(?:live attenuated|inactivated|attenuated|recombinant|subunit|killed|whole-cell|whole cell|vector|conjugate|RNA|DNA|mRNA)\s+', '', cleaned, flags=re.I)
    cleaned = re.sub(r'\s+(?:vaccine|vaccines)$', '', cleaned, flags=re.I)
    cleaned = re.sub(r'\s+(?:subunit|inactivated|attenuated|recombinant|killed|vector|conjugate|RNA|DNA|mRNA)\b.*$', '', cleaned, flags=re.I)
    cleaned = cleaned.strip(' ,;-')

    if cleaned and cleaned.lower() not in {'vaccine', 'vaccines', 'subunit', 'inactivated', 'live attenuated', 'recombinant', 'killed'}:
        if not re.fullmatch(r'^(?:with|using)\s+.*$', cleaned, flags=re.I):
            return cleaned

    if 'btub' in label_lower and 'lptd' in label_lower:
        return 'BtuB and LptD'

    if 'rb51' in label_lower and ('deletion' in label_lower or 'cydc' in label_lower or 'purd' in label_lower):
        return 'RB51 cydC deletion mutant'

    if 'cell wall fraction' in label_lower:
        m = re.search(r'cell wall fraction\s*(?:F\d+|[A-Za-z0-9/.-]+)?', label, flags=re.I)
        if m:
            return m.group(0).strip()

    if 'fraction i' in label_lower:
        return 'fraction I'

    if 'phenol insoluble' in label_lower:
        return 'phenol insoluble fraction'

    if 'ribosomal fraction' in label_lower:
        m = re.search(r'\b(?:strain\s+[A-Za-z0-9/.-]+\s+)?ribosomal fraction\b', label, flags=re.I)
        if m:
            return m.group(0).strip()

    if 'ribosomal protein' in label_lower:
        return 'ribosomal protein'

    if 'aqueous ether extract' in label_lower:
        m = re.search(r'\b(?:strain\s+[A-Za-z0-9/.-]+\s+)?aqueous ether extract\b', label, flags=re.I)
        if m:
            return m.group(0).strip()

    if 'adjuvant' in label_lower:
        m = re.search(r'with\s+([^,;]+?)\s+adjuvant', label, flags=re.I)
        if m:
            return m.group(1).strip()
        m = re.search(r'with\s+([^,;]+)$', label, flags=re.I)
        if m:
            return m.group(1).strip()

    if 'mlta' in label_lower:
        return 'MltA'
    if 'atpd' in label_lower:
        return 'AtpD'
    if 'l7/l12' in label_lower or 'l7l12' in label_lower:
        return 'L7/L12'
    if 'btuB' in label_lower:
        return 'BtuB'
    if 'lptd' in label_lower:
        return 'LptD'

    if description := re.search(r'\b(?:BCSP|rBCSP|BCSP\d+|rBCSP\d+|O-polysaccharide|[A-Z][a-zA-Z0-9/.-]+)\b', label):
        return description.group(0).strip()

    if 'subunit' in label_lower:
        return 'subunit formulation'
    if 'inactivated' in label_lower:
        return 'inactivated formulation'
    if 'live attenuated' in label_lower:
        return 'live attenuated formulation'
    if 'rna' in label_lower or 'dna' in label_lower or 'mrna' in label_lower:
        return 'nucleic acid vaccine platform'

    if definition:
        d = definition
        d = re.sub(r'^(?:a|an|the)\s+', '', d, flags=re.I)
        d = re.sub(r'\b(vaccine|vaccines)\b', '', d, flags=re.I)
        d = d.strip(' ,;:-')
        if d and len(d) < 160:
            return d

    return 'the specific formulation described in this entry'


def fallback_formulation(row):
    label = clean(row.get('LABEL'))
    parent = clean(row.get('Parent (name)')) or label or 'This vaccine'
    if not label:
        return ''
    if parent and label.lower().startswith(parent.lower()):
        remainder = label[len(parent):].strip()
    else:
        remainder = label.strip()
    remainder = remainder.strip(' -;:,. ')
    if not remainder or remainder.lower() in {'vaccine', 'vaccines'}:
        return ''
    if remainder.lower().startswith(('with ', 'using ', 'made ', 'prepared ', 'formulated ', 'containing ', 'carrying ', 'targeting ', 'administered ', 'in ')):
        return remainder
    return 'using ' + remainder


def extract_mutant_description(label: str) -> str:
    label = clean(label)
    if not label:
        return ''
    lower = label.lower()
    if 'delta 31k' in lower or 'delta31k' in lower:
        return 'genetic mutant derived from strain 19 with 31K deletion'
    if 'delta sod' in lower or 'delta-sod' in lower:
        return 'genetic mutant derived from strain 19 with SOD deletion'
    if 'delta vjbr' in lower or 'delta-vjbr' in lower:
        return 'genetic mutant derived from strain 19 with vjbR deletion'
    if 'delta per' in lower or 'delta-per' in lower:
        return 'genetic mutant derived from strain 19 with per deletion'
    if 'mutation' in lower:
        return 'genetic mutant derived from strain 19'
    return ''


def build_definition(row):
    parent = clean(row.get('Parent (name)')) or clean(row.get('LABEL')) or 'This vaccine'
    parent = sentence_case_first(parent).rstrip('. ')
    label = clean(row.get('LABEL'))
    definition = clean(row.get('definition'))
    definition = definition.split('===VACCINE===', 1)[0]

    mutant_desc = extract_mutant_description(label)
    if mutant_desc and 'delta' in label.lower():
        intro = extract_section(definition, 'Vaccine Introduction')
        intro_clean = clean_definition_sentence(intro)
        if intro_clean:
            intro_clean = re.sub(r'^(?:vaccine strain 19 and its genetic mutants were|vaccine strain 19 and its mutant strains were|vaccine strain 19 and its genetic mutant were)', '', intro_clean, flags=re.I)
            intro_clean = intro_clean.strip(' .')
            if 'inoculated' in intro_clean.lower():
                sentence = f'{parent} that is {mutant_desc} was inoculated subcutaneously in calves to study bacterial survival and immune response.'
            else:
                sentence = f'{parent} that is {mutant_desc} was used to study bacterial survival and immune response.'
            return sentence_case_first(sentence)

    formulation = extract_section(definition, 'Vaccine Formulation')
    efficacy = extract_section(definition, 'Vaccine Efficacy')
    immune = extract_section(definition, 'Immune Response')

    if formulation and len(formulation) < 250 and 'not reported' not in formulation.lower() and 'adjuvant' not in formulation.lower() and 'research' not in formulation.lower():
        chosen = formulation
    else:
        chosen = fallback_formulation(row)

    if chosen:
        chosen = chosen.rstrip('. ')
        chosen = chosen.split(';', 1)[0].strip()
        if not re.match(r'^(using|with|made|made as|prepared|composed|consists|containing|carrying|formulated|delivered|administered|expressing|targeting)', chosen, flags=re.I):
            chosen = 'using ' + chosen
        sentence = f'{parent} {chosen}'
    else:
        sentence = parent

    support = []
    for section in [efficacy, immune]:
        if not section:
            continue
        segment = section.split(';')[0].strip()
        segment = re.sub(r'^(The|This|It)\s+', '', segment, flags=re.I)
        segment = segment.rstrip('. ')
        cleaned = clean_definition_sentence(segment)
        if cleaned and not re.search(r'\b(?:not reported|in research|adjuvant|research)\b', cleaned, flags=re.I):
            support.append(cleaned)

    if support:
        sentence = sentence.rstrip('. ') + '. ' + ' '.join(support[:2]).rstrip('. ')

    sentence = re.sub(r'\s+', ' ', sentence)
    sentence = sentence.strip()
    sentence = sentence.split(';', 1)[0].strip()
    if sentence and sentence[-1] != '.':
        sentence += '.'

    segments = [seg.strip() for seg in re.split(r'(?<=\.)\s+', sentence) if seg.strip()]
    final = ' '.join(sentence_case_first(seg) for seg in segments if seg)
    return finalize_definition_text(final)


def extract_definition_detail(row):
    definition = clean(row.get('definition'))
    if not definition:
        return clean(row.get('Parent_Difference'))

    for pattern in [
        r'\b([A-Za-z0-9/.-]+\s+[A-Za-z0-9/.-]+\s+nanoparticles)\b',
        r'\b([A-Za-z0-9/.-]+\s+[A-Za-z0-9/.-]+\s+adjuvant)\b',
        r'\b(using\s+([A-Za-z0-9/.-]+(?:\s+[A-Za-z0-9/.-]+){0,4}))\b',
        r'\b(with\s+([A-Za-z0-9/.-]+(?:\s+[A-Za-z0-9/.-]+){0,4}))\b',
    ]:
        m = re.search(pattern, definition, flags=re.I)
        if m:
            detail = m.group(1).strip()
            if detail.lower() not in {'using', 'with'}:
                return detail

    return clean(row.get('Parent_Difference'))


def ensure_unique_definitions(df):
    seen = set()
    for idx, row in df.iterrows():
        base_text = row['Proposed_VO_Definition']
        parent = clean(row.get('Parent (name)'))
        diff = clean(row.get('Parent_Difference'))
        text = base_text

        if text in seen:
            detail = extract_definition_detail(row)
            if detail and detail.lower() not in text.lower():
                text = f'{sentence_case_first(parent or row.get("LABEL") or "This vaccine")} that is {detail.strip(". ")}.'.rstrip()
            else:
                text = base_text.rstrip('.') + '.'

        seen.add(text)
        df.at[idx, 'Proposed_VO_Definition'] = text
    return df


def assign_vo_ids(df, start_vo_id: int = 9029):
    ids = [f'VO:{i:07d}' for i in range(start_vo_id, start_vo_id + len(df))]
    df['ID'] = ids
    return df


def main():
    df = pd.read_excel(INPUT)

    if 'Parent_Difference' not in df.columns:
        df['Parent_Difference'] = ''
    if 'Proposed_VO_Definition' not in df.columns:
        df['Proposed_VO_Definition'] = ''

    df['Parent_Difference'] = df.apply(lambda row: extract_parent_difference(row), axis=1)
    df['Proposed_VO_Definition'] = df.apply(build_definition, axis=1)
    df['Proposed_VO_Definition'] = df['Proposed_VO_Definition'].apply(finalize_definition_text)
    df = ensure_unique_definitions(df)
    df['Proposed_VO_Definition'] = df['Proposed_VO_Definition'].apply(finalize_definition_text)
    df = assign_vo_ids(df)
    df.to_excel(OUTPUT, index=False)
    print(f'wrote={len(df)} rows')
    print(f'output={OUTPUT}')
    print(df[['ID', 'LABEL', 'Parent (name)', 'Parent_Difference', 'Proposed_VO_Definition']].head(10).to_string(index=False))
    counts = df['Proposed_VO_Definition'].value_counts()
    print('duplicate_definitions', int((counts > 1).sum()))
    print('max_dup', int(counts.max()))


if __name__ == '__main__':
    main()
