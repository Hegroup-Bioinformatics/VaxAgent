# Task 5 Summary: Brucella Vaccine Candidates -> VIOLIN Loading Files

- Source workbook: `outputs/new_brucella_vaccines_rooney_reviewed.xlsx` (sheet `candidate_existing_match`, read-only, not modified)
- Candidate rows reviewed: 760
- Distinct vaccine records after dedup by Standardized Vaccine Name: 682
- Unique PMIDs referenced: 605
- New reference rows created: 577 (new c_reference_id 6867-7443)
- New vaccine rows created: 682 (new c_vaccine_id 8291-8972)
- PMIDs already present in t_reference.csv for pathogen_id=8: 28

## Host resolution
- Target Vaccine Recipient or Host resolved: 209/682 vaccine groups
- Lab Model or Cell resolved to c_model_host: 553/682 vaccine groups

## Items flagged for manual review
- PMIDs with unresolved/incomplete PubMed metadata (0): none
- Vaccine groups with inconsistent Pathogen text across constituent rows (3, informational only since c_pathogen_id is fixed at 8 per prompt instructions):
  - Brucella multi-epitope subunit vaccine: ['Brucella', 'Brucella melitensis; Brucella abortus; Brucella suis; Brucella ovis']
  - Brucella Omp16 subunit vaccine: ['Brucella', 'Brucella spp.']
  - Brucella protective antigen subunit vaccine: ['Brucella', 'Brucella melitensis; Brucella abortus']

## Notes
- c_pathogen_id is fixed at 8 (Brucella spp.) for all new records, per prompts/vaccine-to-violin_prompt.txt.
- Reference tags were embedded directly in c_description at generation time (format `[Ref:c_reference_id]`), since this pipeline has no separate database-load step to defer Task 5 Step 4 to.
- Fields with no confirmable source data (c_full_text, c_vo_id, c_brand_name, c_manufacturer, c_route, c_usage_age, c_preservative, c_allergen, c_cvx_code, c_cvx_desc, c_virulence, c_vector, c_proper_name, c_contraindication, c_location_licensed, c_storage, c_preparation_vo_id) were left blank rather than guessed.
