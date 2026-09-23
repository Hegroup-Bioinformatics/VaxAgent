# Task 5 Summary: Brucella Vaccine Candidates -> VIOLIN Loading Files

- Source workbook: `outputs/new_brucella_vaccines_rooney_reviewed.xlsx` (sheet `candidate_existing_match`, read-only, not modified)
- Candidate rows reviewed: 652
- Distinct vaccine records after dedup by Standardized Vaccine Name: 650
- Unique PMIDs referenced: 523
- New reference rows created: 496 (new c_reference_id 6867-7362)
- New vaccine rows created: 650 (new c_vaccine_id 8291-8940)
- PMIDs already present in t_reference.csv for pathogen_id=8: 27

## Host resolution
- Target Vaccine Recipient or Host resolved: 191/650 vaccine groups
- Lab Model or Cell resolved to c_model_host: 532/650 vaccine groups

## Items flagged for manual review
- PMIDs with unresolved/incomplete PubMed metadata (0): none
- Vaccine groups with inconsistent Pathogen text across constituent rows (0, informational only since c_pathogen_id is fixed at 8 per prompt instructions):

## Manual review: possible non-vaccine source rows (not excluded)
The following source rows were manually reviewed and flagged as possibly not representing a tested vaccine candidate (e.g. pure protein-expression/cloning-only papers, or platform/mechanism-characterization studies with no disease-specific antigen or efficacy data). They remain included in the output above; no rows have been removed. Final inclusion/exclusion is deferred to human review.
- PMID 19824392 — "Expression of human serum albumin--L7/L12 (Brucella abortus ribosomal protein) fusion protein in Saccharomyces cerevisiae" — Pure yeast expression/purification paper; no immune response or efficacy data reported.
- PMID 15390265 — "Engineering of a polymeric bacterial protein as a scaffold for the multiple display of peptides" — Establishes BLS as a generic peptide-display scaffold; no disease-specific antigen selected, no efficacy data.
- PMID 16455994 — "A polymeric bacterial protein activates dendritic cells via TLR4" — Characterizes BLS's intrinsic TLR4-dependent adjuvant mechanism; no foreign antigen, no efficacy data.
- PMID 31380001 — "Cloning and Expression of B. mellitensis bp26 Gene in Lactococcus lactis as a Food Grade Vaccine" — Cloning/expression-only paper; authors explicitly defer vaccine evaluation to future work; no immunization data.

## Notes
- c_pathogen_id is fixed at 8 (Brucella spp.) for all new records, per prompts/vaccine-to-violin_prompt.txt.
- Reference tags were embedded directly in c_description at generation time (format `[Ref:c_reference_id]`), since this pipeline has no separate database-load step to defer Task 5 Step 4 to.
- Fields with no confirmable source data (c_full_text, c_vo_id, c_brand_name, c_manufacturer, c_route, c_usage_age, c_preservative, c_allergen, c_cvx_code, c_cvx_desc, c_virulence, c_vector, c_proper_name, c_contraindication, c_location_licensed, c_storage, c_preparation_vo_id) were left blank rather than guessed.
