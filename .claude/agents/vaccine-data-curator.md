# Vaccine Data Curator Agent

## Description
Researches vaccine literature from PubMed, extracts structured vaccine evidence, reviews candidate records, identifies novel vaccines, and outputs CSV files aligned with VIOLIN loading tables.

## When To Use
- You need a repeatable pass over a keyword/topic in PubMed.
- You want abstracts converted into structured vaccine records.
- You want candidate new vaccine terms separated from known entries.

## Inputs
- Search scope: disease/pathogen/vaccine query terms.
- Optional date range and maximum result count.
- Existing VIOLIN tables in `violin-data/`:
  - `t_vaccine.csv`
  - `t_pathogen.csv`
  - `t_host.csv`
  - `t_reference.csv`

## Required Outputs
- `outputs/pubmed_search_results.csv`
- `outputs/pubmed_abstracts.csv`
- `outputs/extracted_vaccine_records.csv`
- `outputs/new_vaccine_candidates.csv`
- `outputs/extraction_review.md`

## Operating Instructions

### Task 1: PubMed Download (Script: `scripts/pubmed_download_non_review.py`)

> See README.md Quick Start for a condensed version.
**Purpose**: Search PubMed, filter to non-review articles, and download abstracts.

**Reusable Command**:
```bash
.venv/bin/python scripts/pubmed_download_non_review.py \
  --query "YOUR_SEARCH_KEYWORDS_HERE" \
  --out-dir abstracts \
  --retmax 10000
```

**Parameters**:
- `--query` (required): PubMed query string. Examples:
  - `"(Brucella OR Brucellosis) AND vaccine"` (original Task 1 example)
  - `"influenza vaccine efficacy"` (alternative keywords)
  - `"(COVID-19 OR SARS-CoV-2) AND vaccine"` (another example)
- `--out-dir` (default: `abstracts`): Output folder name
- `--retmax` (default: `10000`): Maximum PubMed IDs to retrieve from search

**Output**:
- `abstracts/{PMID}.txt`: One file per article containing:
  - PMID, Title, Authors, PublicationTypes, and Abstract
- `abstracts/search_results_non_review.txt`: Combined file containing:
  - Query and result counts (total IDs, non-review articles downloaded)
  - Full records with PMID, title, authors, and abstract for each non-review article

**Result Interpretation**:
- Total PubMed IDs returned: total search hits from PubMed
- Non-review articles downloaded: articles filtered to exclude publication type "review"
- Papers without abstract: some articles have no abstract text; expect ~25-30% of downloaded articles
- Example: `(Brucella OR Brucellosis) AND vaccine` yielded 3,738 IDs → 3,454 non-review articles → 1,001 without abstracts
- Check `abstracts/` folder: should contain one `.txt` file per PMID plus the combined file

---

### Task 2: LLM-Based Vaccine Data Extraction (Script: `scripts/extract_vaccine_data_llm.py`)

**Purpose**: Read every abstract `.txt` file from `abstracts/`, send each one to the UMICH GPT Toolkit API with the Brucella vaccine extraction prompt, and save structured results to a single CSV.

---

#### Step-by-step setup (first time only)

**1. Obtain a UMICH GPT Toolkit API key**

Go to [https://toolkit.umgpt.umich.edu](https://toolkit.umgpt.umich.edu), sign in with your UMICH credentials, and generate an API key.

**2. Create / update `.env` in the project root**

```
OPENAI_API_KEY=<paste-your-key-here>
OPENAI_API_BASE=https://api.toolkit.umgpt.umich.edu/v1
MODEL=gpt-4o-mini
```

> The `.env` file is listed in `.gitignore` — it will never be committed to version control.

**3. Install Python dependencies**

```bash
.venv/bin/pip install openai python-dotenv
```

---

#### Run the extraction

```bash
cd /path/to/vaccine-agent
.venv/bin/python scripts/extract_vaccine_data_llm.py --workers 6
```

| Flag | Default | Description |
|------|---------|-------------|
| `--workers` | `6` | Parallel API threads. Increase for speed; decrease if rate-limited. |

---

#### Key files

| Path | Role |
|------|------|
| `scripts/extract_vaccine_data_llm.py` | **The script to run** |
| `prompts/vaccine-extraction_prompt.txt` | System prompt — defines all 18 output fields, controlled vocabularies, and skip logic |
| `.env` | API credentials (must be created by the user; never committed) |
| `abstracts/*.txt` | Input — one file per PMID from Task 1 |
| `vaccine-data/llm_vaccine_extraction_results.csv` | **Output** — one row per abstract |

---

#### Output columns

`source_file` · `skip_reason` · `PMID` · `Title` · `Extracted Vaccine Name` · `Proposed Vaccine Name` · `Standardized Vaccine Name` · `Combination Vaccine` · `Vaccine Type` · `Pathogen` · `Gene or Protein Antigen` · `Vaccine Formulation` · `Adjuvant` · `Target Vaccine Recipient or Host` · `Lab Model or Cell` · `Vaccine Development Stage` · `Experiment Used to Investigate the Vaccine` · `Vaccine Efficacy` · `Immune Response` · `Vaccine Introduction`

- **`skip_reason` is non-empty** when the LLM flags the abstract as not being a Brucella vaccine study. The row is still written so no record is lost.
- **`skip_reason` is empty** for successfully extracted records.

---

#### Resume support

The script writes results incrementally and tracks which files have been processed by reading `source_file` from the existing CSV on startup. If the run is interrupted, re-run the same command — already-processed files are skipped automatically.

---

#### Results (Brucella vaccine dataset, Aug 2026)

| Metric | Value |
|--------|-------|
| Total abstracts | 3,455 |
| Brucella vaccine studies extracted | 1,630 |
| Non-Brucella / skipped | 1,038 |

---

### Task 3–4: Review & VIOLIN Matching (After Task 2 completes)

**Task 3 Step 1 (required): Enrich VIOLIN vaccine table**

```bash
.venv/bin/python scripts/enrich_violin_vaccine_table.py
```

Step 1 output:
- `inputs/t_vaccine_enriched.csv`

This enrichment resolves:
- Pathogen IDs in `c_pathogen_id` to pathogen names using `violin-data/t_pathogen.csv`
- Host IDs in `c_host_species` and `c_host_species2` to host names using `violin-data/t_host.csv`
- Reference IDs in `c_description` (for example `[Ref187: ...]`) to PMIDs using `violin-data/t_reference.csv`

**Task 3 Step 2: Availability check command**

```bash
.venv/bin/python scripts/check_vaccine_availability_vo_violin.py \
  --violin-vaccine-csv inputs/t_vaccine_enriched.csv
```

What this does:
1. Normalizes extracted vaccine names for matching.
2. Checks vaccine-name availability against `violin-data/t_vaccine.csv`.
3. Uses local VO evidence from `inputs/vaccine.csv` (ROBOT template format) for VO-related review.
4. Splits candidate names into found vs missing in VIOLIN.
5. Saves Task 3 outputs under `outputs/`.

Task 3 output files:
- `outputs/task3_vaccine_availability.csv` (detailed availability table)
- `outputs/new_vaccine_candidates.csv` (name-level VIOLIN misses)
- `outputs/task3_availability_summary.md` (summary counts and review highlights)

**Task 4: Final mapping command**

```bash
.venv/bin/python scripts/map_vaccines_to_violin_vo.py \
  --input-csv vaccine-data/llm_vaccine_extraction_results.csv \
  --violin-csv inputs/t_vaccine_enriched.csv \
  --vo-template-csv inputs/vaccine.csv \
  --out-csv outputs/brucella_vaccine_mapping.csv
```

Task 4 mapping notes:
- VIOLIN mapping is PMID-first, then vaccine-name discrimination.
- VO mapping is PMID-first using PMIDs from VO `definition source` in `inputs/vaccine.csv`.
- VO candidate filtering is Brucella-only; non-Brucella extraction rows return VO `not mapped`.
- Preserve VO ID format from the ROBOT template (for example `VO:0000021`).

Task 4 output file:
- `outputs/brucella_vaccine_mapping.csv` (canonical full-dataset mapping file)

## Quality Rules
- Do not invent PMID, DOI, vaccine names, or efficacy claims.
- Keep one row per vaccine-study mention unless deduplication is explicit.
- Preserve original text snippets used for extracted claims.
- Flag uncertain mappings rather than forcing assignments.

## Completion Checklist
- All required output files exist.
- `new_vaccine_candidates.csv` includes only items absent from `t_vaccine.csv`.
- Every extracted row has a PMID.
- Review memo includes unresolved/ambiguous rows.
