# Agents

This directory stores reusable agent prompts for this repository.

## Available Agent
- `vaccine-data-curator.md`: end-to-end PubMed abstract extraction workflow that writes review-ready CSV outputs for VIOLIN loading.

## Quick Start: Task 1 (PubMed Download with Custom Keywords)

To download non-review abstracts for any vaccine or disease topic:

```bash
cd /Users/jiezhen/Documents/Ontology/vaccine-agent
.venv/bin/python scripts/pubmed_download_non_review.py \
  --query "YOUR_KEYWORDS" \
  --out-dir abstracts
```

**Output**: 
- One `.txt` file per article (named by PMID)
- Combined `abstracts/search_results_non_review.txt` with all articles and summary counts
- **Note**: ~25-30% of articles typically lack abstracts; these files will have an empty Abstract section

**Real-world Example**:
- Query: `(Brucella OR Brucellosis) AND vaccine`
- PubMed IDs found: 3,738
- Non-review articles: 3,454 (92.4%)
- Papers without abstract: 1,001 (28.9% of downloaded)

**Examples**:
```bash
# Search for influenza vaccine studies
.venv/bin/python scripts/pubmed_download_non_review.py \
  --query "influenza vaccine" \
  --out-dir abstracts

# Search for COVID-19 vaccine literature
.venv/bin/python scripts/pubmed_download_non_review.py \
  --query "(COVID-19 OR SARS-CoV-2) AND vaccine" \
  --out-dir abstracts

# Search for malaria vaccine candidates
.venv/bin/python scripts/pubmed_download_non_review.py \
  --query "malaria vaccine" \
  --out-dir abstracts
```

See `vaccine-data-curator.md` for the full Task 1 documentation and workflow.

---

## Quick Start: Task 2 (LLM-Based Vaccine Data Extraction)

Extracts structured Brucella vaccine fields from the downloaded abstracts using the UMICH GPT Toolkit API.

### Prerequisites

1. **API key** — Obtain a UMICH GPT Toolkit API key from [https://toolkit.umgpt.umich.edu](https://toolkit.umgpt.umich.edu) and add it to `.env` at the project root:

   ```
   OPENAI_API_KEY=<your-key-here>
   OPENAI_API_BASE=https://api.toolkit.umgpt.umich.edu/v1
   MODEL=gpt-4o-mini
   ```

   > **Note**: The `.env` file is listed in `.gitignore` and will never be committed.

2. **Dependencies** — Install into the project virtual environment (one-time):

   ```bash
   .venv/bin/pip install openai python-dotenv
   ```

3. **Abstracts** — Run Task 1 first so that `abstracts/` is populated with `.txt` files.

### Run the Extraction

```bash
cd /Users/jiezhen/Documents/Ontology/vaccine-agent
.venv/bin/python scripts/extract_vaccine_data_llm.py --workers 6
```

- `--workers` (default `6`): number of parallel API threads. Increase for faster throughput if your API quota allows; reduce if you hit rate limits.

### Inputs

| Path | Description |
|------|-------------|
| `abstracts/*.txt` | Abstract files from Task 1 (one per PMID) |
| `prompts/vaccine-extration_prompt.txt` | System prompt with field definitions and controlled vocabularies |
| `.env` | API credentials |

### Output

| Path | Description |
|------|-------------|
| `vaccine-data/llm_vaccine_extraction_results.csv` | One row per abstract, 20 columns |

**Output columns**: `source_file`, `skip_reason`, `PMID`, `Title`, `Extracted Vaccine Name`, `Proposed Vaccine Name`, `Standardized Vaccine Name`, `Combination Vaccine`, `Vaccine Type`, `Pathogen`, `Gene or Protein Antigen`, `Vaccine Formulation`, `Adjuvant`, `Target Vaccine Recipient or Host`, `Lab Model or Cell`, `Vaccine Development Stage`, `Experiment Used to Investigate the Vaccine`, `Vaccine Efficacy`, `Immune Response`, `Vaccine Introduction`

**Skip logic**: Abstracts that the LLM identifies as not studying a Brucella vaccine receive a non-empty `skip_reason` and empty data fields — they are recorded but not lost.

### Resume Support

If the run is interrupted, re-run the same command. Already-processed files (detected by `source_file` in the CSV) are skipped automatically.

### Results from the Brucella Vaccine Dataset

| Metric | Value |
|--------|-------|
| Abstracts processed | 3,455 |
| Brucella vaccine studies (OK) | 1,630 |
| Non-Brucella / skipped | 1,038 |

See `vaccine-data-curator.md` for the full Task 2 documentation.

---

## Quick Start: Task 3 (VO + VIOLIN Availability Check)

Checks extracted vaccine names against the local VIOLIN vaccine table and VO evidence from local ROBOT template data.

### Step 1: Generate Enriched VIOLIN Vaccine Data

This step enriches `t_vaccine.csv` with resolved pathogen names, host species names, and PMIDs extracted from reference IDs in `c_description`.

```bash
cd /Users/jiezhen/Documents/Ontology/vaccine-agent
.venv/bin/python scripts/enrich_violin_vaccine_table.py
```

**Step 1 output**:
- `inputs/t_vaccine_enriched.csv`

### Step 2: Run Task 3 Availability Check

```bash
cd /Users/jiezhen/Documents/Ontology/vaccine-agent
.venv/bin/python scripts/check_vaccine_availability_vo_violin.py \
  --violin-vaccine-csv inputs/t_vaccine_enriched.csv
```

### Inputs

| Path | Description |
|------|-------------|
| `vaccine-data/llm_vaccine_extraction_results.csv` | Task 2 extraction output (default input) |
| `inputs/t_vaccine_enriched.csv` | Enriched VIOLIN vaccine table generated in Step 1 |

### Outputs

| Path | Description |
|------|-------------|
| `outputs/task3_vaccine_availability.csv` | Detailed per-record availability status (VO + VIOLIN) |
| `outputs/new_vaccine_candidates.csv` | Candidate vaccine names not found in VIOLIN |
| `outputs/task3_availability_summary.md` | Task 3 summary counts and top missing candidates |

### Optional Flags

- `--input-csv`: use a different extraction CSV.
- `--disable-vo-lookup`: skip online VO checks and only perform VIOLIN matching.
- `--vo-sleep-sec`: throttle VO API calls (for example `0.1`).

### Task 4: Final Mapping (VIOLIN + VO)

Run full mapping with PMID-first VIOLIN and VO rules:

```bash
cd /Users/jiezhen/Documents/Ontology/vaccine-agent
.venv/bin/python scripts/map_vaccines_to_violin_vo.py \
  --input-csv vaccine-data/brucella_vaccine_llm_extraction_results.csv \
  --violin-csv inputs/t_vaccine_enriched.csv \
  --vo-template-csv inputs/vaccine.csv \
  --out-csv outputs/brucella_vaccine_mapping.csv
```

Task 4 output:
- `outputs/brucella_vaccine_mapping.csv` (canonical full-dataset mapping file)

Notes:
- VO mapping uses `inputs/vaccine.csv` (ROBOT template): row 1 headers, row 2 directives, data from row 3.
- Columns with empty row 2 directives are ignored.
- VO IDs must be preserved in ROBOT format (for example `VO:0000021`).

---

## Full Workflow Invocation

Use the agent for the complete end-to-end workflow with a prompt similar to:

"Process vaccine literature for [TOPIC] from [DATE-START] to [DATE-END], fetch abstracts, extract vaccine records, identify new vaccine candidates relative to `violin-data/t_vaccine.csv`, and write all required outputs under `outputs/`."
