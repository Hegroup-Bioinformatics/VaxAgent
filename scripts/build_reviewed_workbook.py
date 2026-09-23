"""Build outputs/new_brucella_vaccines_reviewed.xlsx by merging Rooney's
annotations (inputs/RK-annotated_new_brucella_vaccines.xlsx) onto the
original outputs/new_brucella_vaccines.xlsx, keyed on PMID + Vaccine Row.

Neither source workbook is modified.
"""
import re

import pandas as pd

ORIGINAL_PATH = "outputs/new_brucella_vaccines.xlsx"
ROONEY_PATH = "inputs/RK-annotated_new_brucella_vaccines.xlsx"
OUTPUT_PATH = "outputs/new_brucella_vaccines_reviewed.xlsx"

KEY_COLS = ["PMID", "Vaccine Row"]


def normalize_combination(raw):
    """Return (clean_value, review_note) for a raw Combination Vaccine cell."""
    if pd.isna(raw):
        return "", ""
    s = str(raw).strip()
    if s == "":
        return "", ""

    note_parts = []
    working = s

    if "-->" in working:
        segments = [seg.strip() for seg in working.split("-->")]
        note_parts.append(f"Rooney changed value: {' --> '.join(segments)}")
        working = segments[-1]

    m = re.search(r"(?i)\s*-\s*rooney notes?:\s*(.+)$", working)
    if m:
        note_parts.append(m.group(1).strip())
        working = working[: m.start()].strip()

    letters_only = re.sub(r"[^a-zA-Z]", "", working).lower()
    if letters_only.startswith("yes"):
        value = "Yes"
    elif letters_only.startswith("no"):
        value = "No"
    else:
        value = ""
        note_parts.append(f"Unrecognized combination value: '{s}'")

    note = "; ".join(p for p in note_parts if p)
    return value, note


def main():
    original_df = pd.read_excel(ORIGINAL_PATH)
    rooney_df = pd.read_excel(ROONEY_PATH)

    assert len(original_df) == len(rooney_df) == 760, "Unexpected row count mismatch"

    original_key = original_df[KEY_COLS].astype(str).agg("||".join, axis=1)
    rooney_key = rooney_df[KEY_COLS].astype(str).agg("||".join, axis=1)
    assert original_key.is_unique, "PMID + Vaccine Row is not unique in original file"
    assert rooney_key.is_unique, "PMID + Vaccine Row is not unique in Rooney file"
    assert set(original_key) == set(rooney_key), "Key sets differ between the two files"
    assert (original_key.values == rooney_key.values).all(), "Row order differs between files"

    out_df = original_df.copy()

    orig_std_name = original_df["Standardized Vaccine Name"]
    rooney_std_name = rooney_df["Standardized Vaccine Name"]
    name_edited = rooney_std_name.astype(str).str.strip() != orig_std_name.astype(str).str.strip()

    orig_combo_raw = original_df["Combination Vaccine"]
    rooney_combo_raw = rooney_df["Combination Vaccine"]
    combo_raw_edited = rooney_combo_raw.astype(str).str.strip() != orig_combo_raw.astype(str).str.strip()

    rooney_notes = rooney_df["ROONEY NOTES"].fillna("")
    notes_present = rooney_notes.astype(str).str.strip() != ""

    combo_clean_and_note = rooney_combo_raw.apply(normalize_combination)
    combo_clean = combo_clean_and_note.apply(lambda t: t[0])
    combo_review_note = combo_clean_and_note.apply(lambda t: t[1])

    # Rows needing real normalization (not already an exact "yes"/"no")
    combo_irregular = ~rooney_combo_raw.astype(str).str.strip().isin(["yes", "no"])

    out_df["Standardized Vaccine Name"] = rooney_std_name
    out_df["Combination Vaccine"] = combo_clean

    out_df["ROONEY NOTES"] = rooney_notes
    out_df["Rooney Edited"] = ((name_edited | combo_raw_edited | notes_present)
                                .map({True: "Yes", False: "No"}))
    out_df["Rooney Name Edited"] = name_edited.map({True: "Yes", False: "No"})
    out_df["Combination Review Note"] = combo_review_note

    for col in [
        "Stella Manual Review",
        "Final New Vaccine Status",
        "Final Standardized Vaccine Name",
        "Review Reason",
        "AI Confidence",
        "Needs Human Check",
    ]:
        out_df[col] = ""

    out_df.to_excel(OUTPUT_PATH, index=False)

    # --- Validation report ---
    final_key = out_df[KEY_COLS].astype(str).agg("||".join, axis=1)
    print(f"Final row count: {len(out_df)}")
    print(f"Unique PMID+Vaccine Row keys: {final_key.nunique()} / {len(out_df)} "
          f"(all 760 preserved uniquely: {final_key.nunique() == 760 and final_key.is_unique})")
    print(f"Rooney Edited = Yes: {(out_df['Rooney Edited'] == 'Yes').sum()}")
    print(f"Rooney Name Edited = Yes: {(out_df['Rooney Name Edited'] == 'Yes').sum()}")
    print(f"Non-empty ROONEY NOTES: {notes_present.sum()}")
    print(f"Normalized Combination Vaccine values changed (irregular raw text): {combo_irregular.sum()}")
    print(f"Output written to: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
