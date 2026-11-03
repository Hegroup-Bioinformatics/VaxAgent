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

### Step 1: Literature Retrieval (Script: `scripts/pubmed_download_non_review.py`)

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

### Step 2: LLM-Based Classification, Metadata Extraction, and Entity Normalization (Script: `scripts/extract_vaccine_data_llm.py`)

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

### Step 3: Iterative Expert Review and Validation

**Step 3.1 Validate study of Brucella Vaccines and Potential New Candidates**

*1. Enrich VIOLIN vaccine table*

```bash
.venv/bin/python scripts/enrich_violin_vaccine_table.py
```

- output:
- `inputs/t_vaccine_enriched.csv`

This enrichment resolves:
- Pathogen IDs in `c_pathogen_id` to pathogen names using `violin-data/t_pathogen.csv`
- Host IDs in `c_host_species` and `c_host_species2` to host names using `violin-data/t_host.csv`
- Reference IDs in `c_description` (for example `[Ref187: ...]`) to PMIDs using `violin-data/t_reference.csv`

*2. Validate Vaccine Availability in VIOLIN and SuggestPotential New Candidates*

**Reusable Prompt**:

The reusable prompt should guide the Agent to normalize entity names, and validate vaccine availability in VIOLIN. 
See `prompts/vaccine-mapping_prompt.txt` for the reusable prompt content.

```bash
.venv/bin/python scripts/check_vaccine_availability_vo_violin.py \
  --violin-vaccine-csv inputs/t_vaccine_enriched.csv
```

What this does:
1. Normalizes extracted vaccine names for matching.
2. Checks vaccine-name availability against `violin-data/t_vaccine.csv`.
3. Uses local VO evidence from `inputs/vaccine.csv` (ROBOT template format) for VO-related review.
4. Splits candidate names into found vs missing in VIOLIN.
5. Saves Step 3 outputs under `outputs/`.

- input files:
- `inputs/t_vaccine_enriched.csv`
- `inputs/vaccine_Brucella.csv`
- `Step2_extraction_brucella_all_abstract_field_extraction_results.csv`

- output file:
- `outputs/Step3.1_Brucella_vaccine_validation_gpt4o_mini.xlsx` (Validated mapping results of Brucella vaccine candidates in VIOLIN)

*3. Manual Review of Brucella Vaccine Candidates*

Manual review should focus on cases where the automated validation is uncertain or where new vaccine candidates are suggested. The reviewer should verify the correctness of mappings and the validity of suggested new candidates. The review process should document any discrepancies or uncertainties for further investigation.

- output file for manual review:
- `outputs/Step3.1_brucella_vaccines_reviewed.xlsx` 

**Step 3.2 Validate New Brucella Vaccine Candidates**

**Reusable Prompt**:
The reusable prompt should guide the Agent to validate whether the article is studied Brucella vaccines and also to identify new vaccine candidates not present in VIOLIN.
See `prompts/vaccine-new-candidates-review_prompt.txt` for the reusable prompt content.

- input file:
- `inputs/Step3.1_Brucella_vaccines_reviewed.xlsx`

- output file:
- `outputs/Step3.2_new_Brucella_vaccines_validation_claude.xlsx`

**Manual Review of Brucella Vaccine Candidates**

Manual review should focus on following fields:
- Vaccine Name
- Pathogen
- Target Vaccine Recipient or Host
- Lab Model or Cell
- Vaccine Type
- Vaccine Development Stage
- Gene or Protein Antigen
- Combination Vaccine

### Step 4: Convert Brucella Vaccine Data to VO vaccine template

**Step 4.1: Convert Brucella Vaccine Data to VO vaccine template format**

**Purpose**: Convert the reviewed Brucella vaccine dataset into the VO-compliant vaccine template and generate the Step 4.1 workbook for downstream ontology curation.

**Reusable Prompt**:
The reusable prompt should guide the Agent to convert the extracted new Brucella vaccines data into a VO-compliant format.
See `prompts/format-conversion-VO.txt` for the reusable prompt content.

**Input**:
- `outputs/Step3.2_new_Brucella_vaccines_reviewed.xlsx`
- `inputs/vaccine_Brucella.csv` (template header and target structure)
- `prompts/vaccine-to-vo_prompt.txt` (mapping and normalization rules)

**Reusable script**:
- `scripts/convert_step4_1_vaccine_to_vo.py`

**Required generation flow**:
1. Load the reviewed workbook and keep only rows where `Vaccine Num` is present.
2. Sort the retained rows by `Vaccine Num` in ascending order.
3. Exclude any row where `Duplicate record` is populated (non-blank). This ensures only master records are exported to the Step 4.1 workbook.
4. Use the exact header row from `inputs/vaccine_Brucella.csv` and populate each target column according to the prompt rules.
5. Apply the global data normalization rules:
   - replace `;` separators with `|`
   - replace any case-insensitive `Not report` value with empty string
   - strip blank and null values
6. For `definition source`, aggregate the current row's `PMID` plus all PMIDs from rows sharing the same `Vaccine Num` through the duplicate relationship. Example:
   - if row `Vaccine Num = 5` has own PMID `749327` and duplicates with `PMID` values `16031427` and `16031388`, the `definition source` should be `PMID:749327|PMID:16031427|PMID:16031388`
7. Add static metadata fields exactly as specified:
   - `term editor = 'VaxAgent | Jie Zheng'`
   - `inSubset = 'vaccine'`
   - `Category = 'VIOLIN'`
8. Populate `Parent (name)` using the pathogen + vaccine type logic, with the priority rules described in the prompt.
9. Save the result as:
   - `outputs/Step4.1_new_Brucella_vaccines_VO.xlsx`

**Run command**:
```bash
.venv/bin/python scripts/convert_step4_1_vaccine_to_vo.py
```

**Expected output**:
- One Excel workbook containing only valid non-duplicate vaccine records in the VO template format.
- `definition source` values should include both the master PMID and all duplicate-related PMIDs for that `Vaccine Num`.

**Validation check**:
- Confirm the exported workbook contains no rows where `Duplicate record` is filled.
- Confirm selected duplicate groups produce aggregated definition-source values like `PMID:749327|PMID:16031427|PMID:16031388`.
- Confirm output file is saved as `.xlsx` only, not CSV.

**Manual Review of Brucella Vaccine Candidates in Step 4.1**

- Review the `Proposed_VO_Definition` column to ensure that each definition is unique and correctly incorporates the unique label phrase when applicable.
- Verify that any appended suffixes like `; with {suffix}` or `(variant n)` are correctly applied and do not duplicate existing information in the definition.
- Check that the `Parent (name)` and other metadata fields are correctly populated according to the rules specified in the prompt. And double-check that any derived values, such as those based on pathogen + vaccine type logic, are the terms used in the VO.
- Ensure that the `definition source` column correctly aggregates PMIDs from both the master record and any duplicate-related records for each `Vaccine Num`. 
- Confirm that the exported workbook maintains the correct order of rows based on `Vaccine Num` and excludes any duplicate records.
- Verify that the static metadata fields such as `term editor`, `inSubset`, and `Category` are correctly populated in the exported workbook.  

**Step 4.2: Generate the final VO template with sequential IDs and textual definitions**

**Main task**: Starting from the reviewed Step 4.1 workbook, generate the final ontology-ready workbook for step 4.2 by keeping all original columns, adding a `Proposed_VO_Definition` field, and assigning VO IDs in sequence. This produces a clean output that is ready for downstream VO review and curation.

**Input**:
- `outputs/Step4.1_new_Brucella_vaccines_VO_reviewed.xlsx`
- `scripts/build_step4_2_vaccine_definitions.py`
- `prompts/vo-vaccine-definition_prompt.txt` (ontology definition guidance and wording constraints)

**Required output**:
- `outputs/Step4.2_new_Brucella_vaccines_template.xlsx`

**Reusable script**:
- `scripts/build_step4_2_vaccine_definitions.py`

**Generation workflow**:
1. Read the Step 4.1 reviewed workbook.
2. Preserve every existing column from the source workbook.
3. Generate a new `Proposed_VO_Definition` for each row using the label plus parent metadata, rather than replacing the original record fields.
4. Apply sentence-case formatting, remove awkward phrasing, and normalize repeated fragments before writing the final definition.
5. When the `LABEL` contains distinguishing details such as antigen, mutant, adjuvant, strain, or target gene names, incorporate those details into the textual definition so it stays specific and unique.
6. Enforce uniqueness of the definitions across the workbook by checking for duplicate text and adding a variant suffix or label-specific qualifier when necessary.
7. Assign sequential VO IDs beginning from `VO:0009029` and populate the `ID` column for every row.
8. Save the final workbook as `.xlsx` only.

**VO ID assignment**:
- Start value: `VO:0009029`
- Format: `VO:{number:07d}`
- Example sequence: `VO:0009029`, `VO:0009030`, `VO:0009031`, ...
- The script writes the IDs in row order, preserving the workbook’s step 4.1 ordering after filtering and cleaning.

**Proposed textual definition rules**:
- The value should be a sentence-style ontology definition, beginning with a capitalized phrase in sentence case.
- It should describe the vaccine in natural, concise language based on the `Parent (name)` and the label details.
- Whenever available, include the distinguishing feature from the label, such as:
  - antigen or target protein name
  - mutant or deletion designations
  - strain names and derived vaccine context
  - adjuvant details or formulation notes
  - RNA/DNA/protein/vector or subunit-type distinctions
- Keep definitions specific enough to distinguish similar vaccine records from one another while avoiding repeated wording across rows.

**Example command**:
```bash
cd /Users/jiezhen/Documents/Ontology/VaxAgent
python scripts/build_step4_2_vaccine_definitions.py
```

**Validation checks**:
- Confirm the output workbook exists at `outputs/Step4.2_new_Brucella_vaccines_template.xlsx`.
- Confirm the first ID is `VO:0009029` and the IDs continue sequentially.
- Confirm `Proposed_VO_Definition` is populated for every row.
- Confirm no duplicate definition strings remain across the workbook.
- Confirm wording is sentence-case and not overly generic.

**Manual Review of Brucella Vaccine Candidates in Step 4.2**
- Review the `Proposed_VO_Definition` column to ensure that each definition is unique and correctly incorporates the unique label phrase when applicable.
- Verify assigned VO IDs are sequential and correctly formatted according to the specified pattern (`VO:{number:07d}`).

---

### Step 5: VIOLIN Database Integration

**Purpose**: Convert the reviewed Brucella vaccines into VIOLIN-compatible tables, reconcile publication references and VO IDs, and prepare host-response records.

#### Inputs

| File | Use |
|------|-----|
| `outputs/Step3.2_new_Brucella_vaccines_reviewed.xlsx` | Final reviewed vaccine candidates and supporting PMIDs |
| `outputs/Step4.2_new_Brucella_vaccines_template_reviewed.xlsx` | Reviewed VO vaccine labels, definitions, and IDs |
| `violin-data/t_vaccine.csv` | VIOLIN vaccine schema and existing records |
| `violin-data/t_reference.csv` | Existing publication/reference records |
| `violin-data/t_host.csv` | Experimental host IDs |
| `violin-data/t_host_response.csv` | Host-response table schema |

#### 5.1. Reference matching

1. Match supporting PMIDs to existing VIOLIN references; prepare records only for missing references.
2. Send the reference import CSV to Leo **without** `c_reference_id`, since the database assigns its own IDs.
3. Use Leo's post-import reference export when preparing vaccine citations. Do not reuse provisional reference IDs from early Task 5/Task 6 drafts.

**Reference import CSV**: `outputs/Step5_new_Brucella_references_VIOLIN.csv` (495 rows, 28 columns; `c_reference_id` omitted). Check that this matches the file Leo received.

**Completed import**: 495 new publication references; existing reference IDs were reused where applicable.

**Historical files**: `outputs/task5_new_references.csv` and `outputs/task5_summary.md` may describe an earlier conversion and are not the final 495-reference import report.

#### 5.2. Vaccine loading

The initial conversion helper is `scripts/build_task5_violin_files.py`. Its original Rooney-reviewed input and preliminary output counts differ from the later finalized Step 3.2/VO mapping. Do **not** regenerate the approved file with the older script without first updating and checking its inputs and mapping rules.

**Final vaccine file**: `outputs/Step5_new_Brucella_vaccines_VIOLIN.csv`.

- 592 distinct vaccine records, 31 columns, in VIOLIN `t_vaccine` format.
- `c_vo_id` is populated from the reviewed Step 4.2 vaccine mapping.
- Reference citations use VIOLIN's assigned reference IDs.
- `c_vaccine_id` is blank in the pre-import file; Leo assigned the database IDs during loading.
- The 592 imported vaccine IDs were reconciled with Leo's `t_vaccine_202610261553.csv` export.

#### 5.3. Host-response preparation

Map the reviewed Step 3.2 experimental evidence into the 21-column `t_host_response` schema:

| Source field | VIOLIN field(s) |
|-------------|-----------------|
| `Lab Model or Cell` | `c_host_id`, `c_host_strain`, `c_cellType` |
| `Vaccine Introduction`; `Experiment Used to Investigate the Vaccine` | `c_vaccination_protocol`, `c_route`, `c_challenge_protocol` |
| `Immune Response` | `c_immune_response` |
| `Vaccine Efficacy` | `c_protection_efficacy` |

**Final handoff file**: `outputs/Step5_Brucella_host_responses_VIOLIN.csv`.

- 429 proposed host-response records, 21 columns.
- `c_vaccine_id` links to Leo's assigned VIOLIN vaccine IDs; `c_host_response_id` remains blank for database assignment.
- Four records from the earlier 433-row draft were withheld after targeted review.
- Jie spot-checked this version and approved sending it to Leo for import on October 29, 2026. Approval to send does not itself confirm that all 429 responses are live in VIOLIN.
- LLM PMID-review experiments generated separate QA suggestions; those suggestions were **not** merged into this file. Further full-text checks can be performed after import.

#### Validation and manual review

1. Confirm 592 vaccine rows and a populated `c_vo_id` for each record.
2. Confirm 429 host-response rows with `c_vaccine_id` and `c_host_id` populated; preserve the 21-column schema.
3. Check vaccine-to-PMID attribution and distinguish vaccination administration routes from pathogen-challenge routes.
4. Keep uncertain experimental details blank rather than inferring them.
5. Verify successful loading with Leo's current VIOLIN database exports before marking host-response integration complete.

**Cleanup note**: Older `task6_*` files, preliminary vaccine/reference CSVs, host-response drafts, and LLM test outputs are working materials. Keep any source evidence needed for traceability, but do not treat these older files as the approved Step 5 loading files.
