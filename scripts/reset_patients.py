"""Reset OpenEMR patient data to a known baseline.

Wipes all patient clinical data (PIDs 4+) and re-inserts 15 synthetic
patients via direct SQL through Docker exec. No REST API calls — fully
deterministic and idempotent.

Patient roster:
  EVAL TEST PATIENTS (PIDs match tests/eval/dataset.json):
    PID 4  — Maria Santos       — T2DM, HTN, obesity, hyperlipidemia
    PID 5  — James Kowalski     — COPD, AFib, T2DM with hyperglycemia, CKD-3
    PID 6  — Aisha Patel        — MDD, hypothyroidism, GAD, penicillin allergy

  EXTENDED CLINICAL SCENARIOS:
    PID 7  — Robert Chen        — HFpEF, AFib, HTN, hyperlipidemia
    PID 8  — Elena Rodriguez    — Rheumatoid arthritis, hypothyroidism, Sjogren's
    PID 9  — Michael Thompson   — CKD stage 4, T2DM, anemia of CKD
    PID 10 — Sarah Kim          — SLE, lupus nephritis, MDD
    PID 11 — William Davis      — COPD, NSCLC on immunotherapy, DVT
    PID 12 — Linda Martinez     — MDD, prediabetes, insomnia
    PID 13 — David Brown        — Parkinson's, Alzheimer's, HTN, osteoporosis
    PID 14 — Patricia Nguyen    — Breast cancer on chemo, neuropathy
    PID 15 — Thomas Wright      — Cirrhosis (HCV), hepatic encephalopathy
    PID 16 — Angela Foster      — Relapsing-remitting MS, neurogenic bladder
    PID 17 — Carlos Rivera      — HIV on ART, latent TB prophylaxis
    PID 18 — Dorothy Hansen     — HFrEF post-MI, ICD, CKD stage 2

Usage:
    uv run python scripts/reset_patients.py
    uv run python scripts/reset_patients.py --dry-run   # print SQL only
    uv run python scripts/reset_patients.py --container my-container-name
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

DOCKER_CONTAINER = os.environ.get("OPENEMR_CONTAINER", "week2_emr_agent-openemr-1")
DB_USER = os.environ.get("OPENEMR_DB_USER", "openemr")
DB_PASS = os.environ.get("OPENEMR_DB_PASS", "openemr")
DB_NAME = os.environ.get("OPENEMR_DB_NAME", "openemr")


# ---------------------------------------------------------------------------
# ID allocation scheme (non-overlapping ranges by table)
# ---------------------------------------------------------------------------
#
#  lists rows      : 90000 + pid * 100  →  pid 4 = 90400-90499, pid 18 = 91800-91899
#  procedure_order : 1000  + pid * 10   →  pid 4 = 1040,        pid 18 = 1180
#  procedure_report: same IDs as procedure_order
#  procedure_result: 10000 + pid * 100  →  pid 4 = 10400-10499, pid 18 = 11800-11899
#  form_encounter  : 80000 + pid * 10   →  pid 4 = 80040,       pid 18 = 80180
#  forms           : same IDs as form_encounter


def _uid() -> str:
    """Return a fresh UUID as uppercase 32-char hex for UNHEX() insertion."""
    return uuid.uuid4().hex.upper()


# ---------------------------------------------------------------------------
# Synthetic patient definitions
# ---------------------------------------------------------------------------

PATIENTS: list[dict] = [
    # ── PID 4: Maria Santos ──────────────────────────────────────────────
    # Eval patient. T2DM poorly controlled, HTN, obesity, hyperlipidemia.
    # Referenced by eval cases: hp-02, hp-03, hp-05, hp-13, hp-14, hp-17,
    # dsl-03, dsl-06, dsl-08, dsl-16, cp-02, cp-05, cp-11
    {
        "pid": 4,
        "fname": "Maria", "lname": "Santos",
        "dob": "1985-03-14", "sex": "Female",
        "street": "742 Evergreen Terrace", "city": "Springfield",
        "state": "IL", "postal_code": "62704",
        "phone_home": "217-555-0142", "ss": "123-45-6789",
        "email": "maria.santos@example.com",
        "race": "white", "ethnicity": "hisp_or_latin",
        "conditions": [
            # (title, ICD10, begdate)
            ("Type 2 Diabetes Mellitus", "E11.9", "2020-01-15"),
            ("Essential Hypertension", "I10", "2019-06-01"),
            ("Obesity, Morbid, due to Excess Calories", "E66.01", "2021-03-10"),
            ("Pure Hypercholesterolemia", "E78.00", "2020-08-15"),
        ],
        "medications": [
            # (title, begdate)
            ("Metformin 500mg twice daily", "2020-02-01"),
            ("Lisinopril 10mg daily", "2019-07-01"),
            ("Atorvastatin 20mg daily at bedtime", "2020-09-01"),
        ],
        "allergies": [],
        # labs: (order_date, [(result_code, result_text, value, units, ref_range, abnormal)])
        "labs": [
            ("2025-10-15", [
                ("4548-4",  "Hemoglobin A1c",     "8.2",  "%",      "4.0-5.6",  "high"),
                ("2345-7",  "Glucose",             "168",  "mg/dL",  "70-99",    "high"),
                ("2089-1",  "LDL Cholesterol",     "145",  "mg/dL",  "<100",     "high"),
                ("2160-0",  "Creatinine",          "0.9",  "mg/dL",  "0.6-1.2",  ""),
            ]),
        ],
        "encounters": [
            ("2025-10-20 09:00:00", "Diabetes and hypertension follow-up"),
        ],
    },

    # ── PID 5: James Kowalski ────────────────────────────────────────────
    # Eval patient. COPD, AFib on anticoagulation, T2DM, CKD stage 3.
    # Referenced by eval cases: hp-07, hp-09, hp-12, hp-16,
    # dsl-05, dsl-08, dsl-14, cp-01, cp-06, cp-07, cp-08, cp-12
    {
        "pid": 5,
        "fname": "James", "lname": "Kowalski",
        "dob": "1958-11-02", "sex": "Male",
        "street": "310 Oak Lane", "city": "Madison",
        "state": "WI", "postal_code": "53703",
        "phone_home": "608-555-0198", "ss": "987-65-4321",
        "email": "j.kowalski@example.com",
        "race": "white", "ethnicity": "not_hisp_or_latin",
        "conditions": [
            ("Chronic Obstructive Pulmonary Disease with Acute Exacerbation", "J44.1", "2018-03-10"),
            ("Unspecified Atrial Fibrillation", "I48.91", "2019-09-15"),
            ("Type 2 Diabetes Mellitus with Hyperglycemia", "E11.65", "2021-05-20"),
            ("Chronic Kidney Disease, Stage 3", "N18.3", "2022-11-01"),
        ],
        "medications": [
            ("Tiotropium 18mcg inhaled once daily", "2018-04-01"),
            ("Apixaban 5mg twice daily", "2019-10-01"),
            ("Metformin 1000mg twice daily", "2021-06-01"),
            ("Albuterol 90mcg inhaled PRN", "2018-04-01"),
        ],
        "allergies": [],
        "labs": [
            ("2025-09-20", [
                ("42637-9", "BNP",                  "385",  "pg/mL",        "0-100",  "high"),
                ("2160-0",  "Creatinine",            "2.1",  "mg/dL",        "0.7-1.3","high"),
                ("33914-3", "eGFR",                  "38",   "mL/min/1.73m2",">=60",   "low"),
                ("4548-4",  "Hemoglobin A1c",        "9.1",  "%",            "4.0-5.6","high"),
            ]),
        ],
        "encounters": [
            ("2025-09-25 14:00:00", "COPD exacerbation and atrial fibrillation management"),
        ],
    },

    # ── PID 6: Aisha Patel ───────────────────────────────────────────────
    # Eval patient. MDD recurrent, hypothyroidism, GAD. Penicillin allergy.
    # Referenced by eval cases: hp-10, hp-15, hp-18, hp-19, dsl-01, dsl-07,
    # dsl-15, cp-03, cp-04, cp-09, oq-08, oq-10, oq-12
    {
        "pid": 6,
        "fname": "Aisha", "lname": "Patel",
        "dob": "1972-07-28", "sex": "Female",
        "street": "88 Birch Street", "city": "Austin",
        "state": "TX", "postal_code": "78701",
        "phone_home": "512-555-0167", "ss": "456-78-9012",
        "email": "aisha.patel@example.com",
        "race": "asian", "ethnicity": "not_hisp_or_latin",
        "conditions": [
            ("Major Depressive Disorder, Recurrent, Moderate", "F33.1", "2022-02-14"),
            ("Hypothyroidism, Unspecified", "E03.9", "2021-08-05"),
            ("Generalized Anxiety Disorder", "F41.1", "2022-03-01"),
        ],
        "medications": [
            ("Sertraline 100mg daily", "2022-03-01"),
            ("Levothyroxine 75mcg daily on empty stomach", "2021-09-01"),
        ],
        "allergies": [
            # (title, begdate)
            ("Penicillin", "2010-05-12"),
        ],
        "labs": [
            ("2025-11-03", [
                ("11579-0", "TSH",                 "6.8",  "mIU/L",  "0.4-4.0", "high"),
                ("3024-7",  "Free T4",             "0.7",  "ng/dL",  "0.8-1.8", "low"),
            ]),
        ],
        "encounters": [
            ("2025-11-05 11:00:00", "Depression and thyroid management follow-up"),
        ],
    },

    # ── PID 7: Robert Chen ───────────────────────────────────────────────
    # HFpEF, AFib on anticoagulation, HTN, hypercholesterolemia.
    # Complex polypharmacy; amiodarone allergy (thyroid toxicity).
    {
        "pid": 7,
        "fname": "Robert", "lname": "Chen",
        "dob": "1952-08-19", "sex": "Male",
        "street": "1420 Harbor View Drive", "city": "San Francisco",
        "state": "CA", "postal_code": "94103",
        "phone_home": "415-555-0231", "ss": "321-54-9876",
        "email": "robert.chen@example.com",
        "race": "asian", "ethnicity": "not_hisp_or_latin",
        "conditions": [
            ("Heart Failure with Preserved Ejection Fraction", "I50.30", "2021-04-10"),
            ("Unspecified Atrial Fibrillation", "I48.91", "2020-07-15"),
            ("Essential Hypertension", "I10", "2018-01-20"),
            ("Pure Hypercholesterolemia", "E78.00", "2019-03-05"),
        ],
        "medications": [
            ("Carvedilol 25mg twice daily", "2021-05-01"),
            ("Furosemide 40mg daily", "2021-04-15"),
            ("Spironolactone 25mg daily", "2021-04-15"),
            ("Atorvastatin 80mg daily at bedtime", "2019-03-10"),
            ("Rivaroxaban 20mg daily with evening meal", "2020-08-01"),
        ],
        "allergies": [
            ("Amiodarone", "2020-09-01"),
        ],
        "labs": [
            ("2025-10-08", [
                ("42637-9", "BNP",             "620",  "pg/mL",  "0-100",   "high"),
                ("2951-2",  "Sodium",          "138",  "mEq/L",  "136-145", ""),
                ("2160-0",  "Creatinine",      "1.4",  "mg/dL",  "0.7-1.3", "high"),
                ("6301-6",  "INR",             "1.1",  "",        "0.8-1.2", ""),
            ]),
        ],
        "encounters": [
            ("2025-10-10 10:00:00", "Heart failure monitoring and fluid status assessment"),
        ],
    },

    # ── PID 8: Elena Rodriguez ───────────────────────────────────────────
    # Rheumatoid arthritis on DMARDs, secondary hypothyroidism, Sjogren's.
    # Immunosuppressed; sulfonamide allergy.
    {
        "pid": 8,
        "fname": "Elena", "lname": "Rodriguez",
        "dob": "1979-12-03", "sex": "Female",
        "street": "567 Maple Grove Road", "city": "Denver",
        "state": "CO", "postal_code": "80203",
        "phone_home": "720-555-0384", "ss": "654-32-1098",
        "email": "elena.rodriguez@example.com",
        "race": "white", "ethnicity": "hisp_or_latin",
        "conditions": [
            ("Rheumatoid Arthritis, Unspecified", "M06.9", "2017-09-22"),
            ("Hypothyroidism, Unspecified", "E03.9", "2019-04-11"),
            ("Sjogren Syndrome with Other Organ Involvement", "M35.09", "2020-01-08"),
        ],
        "medications": [
            ("Methotrexate 15mg orally once weekly", "2017-10-15"),
            ("Folic Acid 1mg daily (methotrexate supplement)", "2017-10-15"),
            ("Hydroxychloroquine 400mg daily", "2018-02-01"),
            ("Levothyroxine 50mcg daily on empty stomach", "2019-05-01"),
            ("Prednisone 5mg daily", "2021-06-01"),
        ],
        "allergies": [
            ("Trimethoprim-Sulfamethoxazole", "2015-03-20"),
        ],
        "labs": [
            ("2025-08-22", [
                ("RF",      "Rheumatoid Factor",   "128",  "IU/mL",  "<14",     "high"),
                ("CRP",     "C-Reactive Protein",  "2.4",  "mg/L",   "<1.0",    "high"),
                ("11579-0", "TSH",                 "1.8",  "mIU/L",  "0.4-4.0", ""),
                ("718-7",   "Hemoglobin",          "11.8", "g/dL",   "12.0-16.0","low"),
            ]),
        ],
        "encounters": [
            ("2025-08-25 09:30:00", "RA disease activity assessment and immunosuppression monitoring"),
        ],
    },

    # ── PID 9: Michael Thompson ──────────────────────────────────────────
    # CKD stage 4 near dialysis threshold, T2DM, HTN, anemia of CKD.
    # Renal dose adjustment required; contrast allergy.
    {
        "pid": 9,
        "fname": "Michael", "lname": "Thompson",
        "dob": "1966-05-30", "sex": "Male",
        "street": "2210 Riverside Boulevard", "city": "Cleveland",
        "state": "OH", "postal_code": "44101",
        "phone_home": "216-555-0519", "ss": "789-01-2345",
        "email": "m.thompson@example.com",
        "race": "white", "ethnicity": "not_hisp_or_latin",
        "conditions": [
            ("Chronic Kidney Disease, Stage 4", "N18.4", "2020-02-14"),
            ("Type 2 Diabetes Mellitus", "E11.9", "2016-07-20"),
            ("Essential Hypertension", "I10", "2015-03-18"),
            ("Anemia in Chronic Kidney Disease", "N18.9", "2021-01-10"),
        ],
        "medications": [
            ("Amlodipine 10mg daily", "2015-04-01"),
            ("Insulin Glargine 20 units subcutaneous at bedtime", "2020-08-01"),
            ("Sodium Bicarbonate 650mg twice daily", "2021-03-01"),
            ("Epoetin Alfa 4000 units subcutaneous three times weekly", "2021-02-01"),
        ],
        "allergies": [
            ("Iodinated Contrast Dye", "2020-02-20"),
        ],
        "labs": [
            ("2025-11-10", [
                ("33914-3", "eGFR",           "22",   "mL/min/1.73m2", ">=60",    "low"),
                ("2160-0",  "Creatinine",     "3.2",  "mg/dL",         "0.7-1.3", "high"),
                ("718-7",   "Hemoglobin",     "9.1",  "g/dL",          "13.5-17.5","low"),
                ("2823-3",  "Potassium",      "5.2",  "mEq/L",         "3.5-5.0", "high"),
                ("4548-4",  "Hemoglobin A1c", "7.9",  "%",             "4.0-5.6", "high"),
            ]),
        ],
        "encounters": [
            ("2025-11-12 13:00:00", "CKD stage 4 monitoring, anemia management, and dialysis planning"),
        ],
    },

    # ── PID 10: Sarah Kim ────────────────────────────────────────────────
    # SLE with active lupus nephritis, MDD. Young female on immunosuppression.
    # Multiple drug allergies: penicillin (anaphylaxis), NSAIDs (lupus flare).
    {
        "pid": 10,
        "fname": "Sarah", "lname": "Kim",
        "dob": "1996-02-17", "sex": "Female",
        "street": "945 Cherry Blossom Lane", "city": "Seattle",
        "state": "WA", "postal_code": "98101",
        "phone_home": "206-555-0672", "ss": "012-34-5678",
        "email": "sarah.kim@example.com",
        "race": "asian", "ethnicity": "not_hisp_or_latin",
        "conditions": [
            ("Systemic Lupus Erythematosus", "M32.9", "2019-11-05"),
            ("Lupus Nephritis", "M32.14", "2020-03-15"),
            ("Major Depressive Disorder, Single Episode, Moderate", "F32.1", "2021-01-20"),
        ],
        "medications": [
            ("Hydroxychloroquine 200mg twice daily", "2019-11-20"),
            ("Mycophenolate Mofetil 1g twice daily", "2020-04-01"),
            ("Prednisone 10mg daily (tapering)", "2020-03-20"),
            ("Sertraline 50mg daily", "2021-02-01"),
        ],
        "allergies": [
            ("Penicillin", "2008-06-14"),
            ("Ibuprofen", "2020-01-05"),
        ],
        "labs": [
            ("2025-09-14", [
                ("2160-0",  "Creatinine",          "1.8",  "mg/dL",   "0.5-1.1",  "high"),
                ("C3",      "Complement C3",       "68",   "mg/dL",   "90-180",   "low"),
                ("C4",      "Complement C4",       "12",   "mg/dL",   "16-47",    "low"),
                ("33914-3", "eGFR",                "41",   "mL/min/1.73m2",">=60", "low"),
            ]),
        ],
        "encounters": [
            ("2025-09-16 10:30:00", "Lupus nephritis activity monitoring and mental health follow-up"),
        ],
    },

    # ── PID 11: William Davis ────────────────────────────────────────────
    # COPD, NSCLC (right upper lobe) on pembrolizumab immunotherapy, DVT on anticoagulation.
    # Complex oncology patient; codeine allergy (respiratory depression).
    {
        "pid": 11,
        "fname": "William", "lname": "Davis",
        "dob": "1959-09-14", "sex": "Male",
        "street": "1855 Elm Street", "city": "Pittsburgh",
        "state": "PA", "postal_code": "15201",
        "phone_home": "412-555-0784", "ss": "135-79-2468",
        "email": "w.davis@example.com",
        "race": "white", "ethnicity": "not_hisp_or_latin",
        "conditions": [
            ("Chronic Obstructive Pulmonary Disease, Unspecified", "J44.0", "2016-05-08"),
            ("Malignant Neoplasm of Upper Lobe, Right Bronchus and Lung", "C34.10", "2023-02-20"),
            ("Acute Deep Vein Thrombosis of Right Femoral Vein", "I82.401", "2023-08-15"),
        ],
        "medications": [
            ("Tiotropium 18mcg inhaled once daily", "2016-06-01"),
            ("Pembrolizumab 200mg intravenous every 3 weeks", "2023-03-15"),
            ("Rivaroxaban 15mg twice daily with food", "2023-08-20"),
            ("Dexamethasone 4mg orally before pembrolizumab infusion", "2023-03-15"),
        ],
        "allergies": [
            ("Codeine", "2005-11-28"),
        ],
        "labs": [
            ("2025-10-25", [
                ("CEA",     "Carcinoembryonic Antigen", "12.4", "ng/mL",  "0-3.0",    "high"),
                ("718-7",   "Hemoglobin",               "10.8", "g/dL",   "13.5-17.5","low"),
                ("10839-9", "D-Dimer",                  "2.8",  "mg/L FEU","<0.5",    "high"),
                ("1751-7",  "Albumin",                  "3.4",  "g/dL",   "3.5-5.0",  "low"),
            ]),
        ],
        "encounters": [
            ("2025-10-28 11:00:00", "Pembrolizumab cycle 12 monitoring and DVT anticoagulation review"),
        ],
    },

    # ── PID 12: Linda Martinez ───────────────────────────────────────────
    # MDD in first episode, prediabetes, insomnia. Starting antidepressant.
    # Metabolic monitoring needed; no allergies.
    {
        "pid": 12,
        "fname": "Linda", "lname": "Martinez",
        "dob": "1969-11-25", "sex": "Female",
        "street": "304 Sunset Boulevard", "city": "Phoenix",
        "state": "AZ", "postal_code": "85001",
        "phone_home": "602-555-0893", "ss": "246-80-1357",
        "email": "linda.martinez@example.com",
        "race": "white", "ethnicity": "hisp_or_latin",
        "conditions": [
            ("Major Depressive Disorder, Single Episode, Moderate", "F32.1", "2023-01-10"),
            ("Prediabetes", "R73.09", "2022-09-20"),
            ("Insomnia, Unspecified", "G47.00", "2023-01-10"),
        ],
        "medications": [
            ("Venlafaxine 150mg extended-release daily", "2023-02-01"),
            ("Metformin 500mg twice daily with meals", "2023-01-15"),
            ("Trazodone 50mg at bedtime PRN insomnia", "2023-01-15"),
        ],
        "allergies": [],
        "labs": [
            ("2025-10-02", [
                ("1558-6",  "Fasting Glucose",  "118",  "mg/dL", "70-99",   "high"),
                ("4548-4",  "Hemoglobin A1c",   "6.1",  "%",     "4.0-5.6", "high"),
                ("11579-0", "TSH",              "2.1",  "mIU/L", "0.4-4.0", ""),
                ("1742-6",  "ALT",              "22",   "U/L",   "7-40",    ""),
            ]),
        ],
        "encounters": [
            ("2025-10-05 09:00:00", "Depression and metabolic syndrome management follow-up"),
        ],
    },

    # ── PID 13: David Brown ──────────────────────────────────────────────
    # Parkinson's disease, Alzheimer's dementia, HTN, osteoporosis.
    # Elderly male; haloperidol allergy (worsens parkinsonism).
    {
        "pid": 13,
        "fname": "David", "lname": "Brown",
        "dob": "1944-04-07", "sex": "Male",
        "street": "72 Willow Creek Court", "city": "Sarasota",
        "state": "FL", "postal_code": "34230",
        "phone_home": "941-555-0945", "ss": "369-25-8147",
        "email": "d.brown@example.com",
        "race": "white", "ethnicity": "not_hisp_or_latin",
        "conditions": [
            ("Parkinson's Disease", "G20", "2018-06-15"),
            ("Alzheimer's Disease, Unspecified", "G30.9", "2020-10-01"),
            ("Essential Hypertension", "I10", "2010-03-22"),
            ("Osteoporosis without Current Pathological Fracture", "M81.0", "2019-08-14"),
        ],
        "medications": [
            ("Carbidopa-Levodopa 25/100mg three times daily", "2018-07-01"),
            ("Memantine 10mg twice daily", "2020-11-01"),
            ("Donepezil 10mg daily at bedtime", "2020-11-01"),
            ("Lisinopril 5mg daily", "2010-04-01"),
            ("Alendronate 70mg orally once weekly on empty stomach", "2019-09-01"),
        ],
        "allergies": [
            ("Haloperidol", "2019-01-15"),
        ],
        "labs": [
            ("2025-09-30", [
                ("11579-0", "TSH",              "3.2",   "mIU/L",  "0.4-4.0",  ""),
                ("2132-9",  "Vitamin B12",      "412",   "pg/mL",  "200-900",  ""),
                ("DEXA",    "DEXA T-score (Lumbar Spine)", "-2.8", "", ">-1.0", "low"),
                ("2160-0",  "Creatinine",       "1.0",   "mg/dL",  "0.7-1.3",  ""),
            ]),
        ],
        "encounters": [
            ("2025-10-01 10:00:00", "Parkinson's and dementia management, fall risk assessment"),
        ],
    },

    # ── PID 14: Patricia Nguyen ──────────────────────────────────────────
    # Stage II invasive ductal carcinoma (left breast), on weekly paclitaxel.
    # Chemotherapy-induced peripheral neuropathy and nausea. Taxane premedication.
    {
        "pid": 14,
        "fname": "Patricia", "lname": "Nguyen",
        "dob": "1971-06-22", "sex": "Female",
        "street": "1188 Pacific Coast Highway", "city": "Los Angeles",
        "state": "CA", "postal_code": "90001",
        "phone_home": "213-555-1042", "ss": "147-25-8036",
        "email": "p.nguyen@example.com",
        "race": "asian", "ethnicity": "not_hisp_or_latin",
        "conditions": [
            ("Malignant Neoplasm of Lower-Inner Quadrant of Left Female Breast", "C50.312", "2025-03-10"),
            ("Drug-Induced Peripheral Neuropathy", "G62.0", "2025-07-15"),
            ("Chemotherapy-Induced Nausea and Vomiting", "R11.2", "2025-04-01"),
        ],
        "medications": [
            ("Paclitaxel 80mg/m2 intravenous weekly (current cycle)", "2025-04-01"),
            ("Ondansetron 8mg orally PRN nausea", "2025-04-01"),
            ("Gabapentin 300mg three times daily", "2025-07-20"),
            ("Tamoxifen 20mg daily (post-chemotherapy)", "2025-09-01"),
        ],
        "allergies": [
            ("Docetaxel", "2025-04-15"),
        ],
        "labs": [
            ("2025-11-05", [
                ("718-7",   "Hemoglobin",               "10.2", "g/dL",   "12.0-16.0", "low"),
                ("6690-2",  "White Blood Cell Count",   "3.1",  "K/µL",   "4.5-11.0",  "low"),
                ("777-3",   "Platelet Count",           "185",  "K/µL",   "150-400",   ""),
                ("CA153",   "Cancer Antigen 15-3",      "42",   "U/mL",   "0-31",      "high"),
                ("1742-6",  "ALT",                      "28",   "U/L",    "7-40",      ""),
            ]),
        ],
        "encounters": [
            ("2025-11-07 08:30:00", "Paclitaxel cycle 10 infusion and neuropathy assessment"),
        ],
    },

    # ── PID 15: Thomas Wright ────────────────────────────────────────────
    # Hepatitis C-related cirrhosis (Child-Pugh B), hepatic encephalopathy grade 2,
    # known esophageal varices, portal hypertension. MELD score ~18.
    {
        "pid": 15,
        "fname": "Thomas", "lname": "Wright",
        "dob": "1963-01-30", "sex": "Male",
        "street": "530 Harbor Street", "city": "New Orleans",
        "state": "LA", "postal_code": "70112",
        "phone_home": "504-555-1153", "ss": "258-36-9147",
        "email": "t.wright@example.com",
        "race": "white", "ethnicity": "not_hisp_or_latin",
        "conditions": [
            ("Alcoholic Cirrhosis of Liver with Ascites", "K70.31", "2021-08-05"),
            ("Hepatic Encephalopathy", "K72.10", "2023-05-12"),
            ("Esophageal Varices without Bleeding", "I85.00", "2022-02-18"),
            ("Portal Hypertension", "K76.6", "2021-08-05"),
        ],
        "medications": [
            ("Lactulose 30mL orally twice daily (titrate to 2-3 soft stools/day)", "2023-05-15"),
            ("Rifaximin 550mg twice daily", "2023-06-01"),
            ("Propranolol 20mg twice daily (varices prophylaxis)", "2022-03-01"),
            ("Spironolactone 100mg daily", "2021-09-01"),
            ("Furosemide 40mg daily", "2021-09-01"),
        ],
        "allergies": [
            ("Amoxicillin-Clavulanate", "2022-11-03"),
        ],
        "labs": [
            ("2025-11-12", [
                ("1975-2",  "Total Bilirubin",  "4.2",  "mg/dL",  "0.1-1.2",  "high"),
                ("1742-6",  "ALT",              "68",   "U/L",    "7-40",     "high"),
                ("6301-6",  "INR",              "2.1",  "",        "0.8-1.2",  "high"),
                ("1751-7",  "Albumin",          "2.8",  "g/dL",   "3.5-5.0",  "low"),
                ("777-3",   "Platelet Count",   "72",   "K/µL",   "150-400",  "low"),
            ]),
        ],
        "encounters": [
            ("2025-11-14 11:30:00", "Hepatic encephalopathy monitoring and cirrhosis complication management"),
        ],
    },

    # ── PID 16: Angela Foster ────────────────────────────────────────────
    # Relapsing-remitting multiple sclerosis on ocrelizumab, neurogenic bladder,
    # spasticity. Interferon beta caused severe depression — allergic.
    {
        "pid": 16,
        "fname": "Angela", "lname": "Foster",
        "dob": "1984-09-11", "sex": "Female",
        "street": "2701 University Avenue", "city": "Baltimore",
        "state": "MD", "postal_code": "21201",
        "phone_home": "410-555-1267", "ss": "370-48-1926",
        "email": "angela.foster@example.com",
        "race": "black_or_african_american", "ethnicity": "not_hisp_or_latin",
        "conditions": [
            ("Relapsing Remitting Multiple Sclerosis", "G35", "2015-04-20"),
            ("Neurogenic Bladder", "N31.9", "2016-08-10"),
            ("Muscle Spasticity", "R25.2", "2016-08-10"),
        ],
        "medications": [
            ("Ocrelizumab 600mg intravenous every 6 months", "2019-05-01"),
            ("Oxybutynin 5mg twice daily", "2016-09-01"),
            ("Baclofen 10mg three times daily", "2016-09-01"),
            ("Vitamin D3 4000 IU daily", "2015-06-01"),
            ("Dalfampridine 10mg twice daily (walking speed)", "2018-01-15"),
        ],
        "allergies": [
            ("Interferon Beta-1a", "2016-02-10"),
        ],
        "labs": [
            ("2025-10-18", [
                ("IgGIdx",  "CSF IgG Index",    "0.82", "",        "0.28-0.66", "high"),
                ("718-7",   "Hemoglobin",       "13.2", "g/dL",   "12.0-16.0", ""),
                ("11579-0", "TSH",              "2.8",  "mIU/L",  "0.4-4.0",   ""),
                ("2160-0",  "Creatinine",       "0.7",  "mg/dL",  "0.5-1.1",   ""),
            ]),
        ],
        "encounters": [
            ("2025-10-20 09:00:00", "MS ocrelizumab infusion day and neurological status review"),
        ],
    },

    # ── PID 17: Carlos Rivera ────────────────────────────────────────────
    # HIV infection (viral load undetectable on BIC/FTC/TAF), latent TB on INH,
    # drug-induced dyslipidemia. HLA-B*5701 positive — abacavir allergy.
    {
        "pid": 17,
        "fname": "Carlos", "lname": "Rivera",
        "dob": "1981-03-07", "sex": "Male",
        "street": "892 Mission Street", "city": "Miami",
        "state": "FL", "postal_code": "33101",
        "phone_home": "305-555-1374", "ss": "481-60-2537",
        "email": "c.rivera@example.com",
        "race": "white", "ethnicity": "hisp_or_latin",
        "conditions": [
            ("Human Immunodeficiency Virus (HIV) Disease", "B20", "2018-09-14"),
            ("Latent Tuberculosis Infection", "Z22.7", "2024-01-20"),
            ("Drug-Induced Hyperlipidemia", "E78.5", "2020-06-01"),
        ],
        "medications": [
            ("Bictegravir/Emtricitabine/Tenofovir Alafenamide 50/200/25mg daily", "2018-10-01"),
            ("Isoniazid 300mg daily (9-month LTBI course)", "2024-02-01"),
            ("Pyridoxine 50mg daily (isoniazid supplement)", "2024-02-01"),
            ("Rosuvastatin 10mg daily (evening)", "2020-07-01"),
        ],
        "allergies": [
            ("Abacavir", "2018-09-20"),
        ],
        "labs": [
            ("2025-11-08", [
                ("HIVRNA",  "HIV Viral Load",     "<20",  "copies/mL",  "<20",    ""),
                ("CD4",     "CD4 T-Cell Count",   "524",  "cells/µL",   ">500",   ""),
                ("2089-1",  "LDL Cholesterol",    "168",  "mg/dL",      "<100",   "high"),
                ("2571-8",  "Triglycerides",      "285",  "mg/dL",      "<150",   "high"),
                ("1742-6",  "ALT",                "34",   "U/L",        "7-40",   ""),
            ]),
        ],
        "encounters": [
            ("2025-11-10 10:00:00", "HIV viral load check, LTBI compliance, and lipid management"),
        ],
    },

    # ── PID 18: Dorothy Hansen ───────────────────────────────────────────
    # HFrEF (EF 30%), 6 months post-anterior MI with drug-eluting stent,
    # ventricular tachycardia (ICD implanted), CKD stage 2. ACE inhibitor
    # allergy (angioedema) — on sacubitril/valsartan instead.
    {
        "pid": 18,
        "fname": "Dorothy", "lname": "Hansen",
        "dob": "1950-12-19", "sex": "Female",
        "street": "4430 Prairie Wind Road", "city": "Omaha",
        "state": "NE", "postal_code": "68101",
        "phone_home": "402-555-1498", "ss": "593-72-8046",
        "email": "d.hansen@example.com",
        "race": "white", "ethnicity": "not_hisp_or_latin",
        "conditions": [
            ("Heart Failure with Reduced Ejection Fraction", "I50.20", "2025-05-01"),
            ("Old Myocardial Infarction", "I25.2", "2025-05-01"),
            ("Ventricular Tachycardia", "I47.2", "2025-06-10"),
            ("Chronic Kidney Disease, Stage 2", "N18.2", "2025-05-01"),
        ],
        "medications": [
            ("Metoprolol Succinate 50mg extended-release daily", "2025-05-15"),
            ("Sacubitril-Valsartan 24/26mg twice daily", "2025-05-15"),
            ("Eplerenone 25mg daily", "2025-06-01"),
            ("Rosuvastatin 40mg daily at bedtime", "2025-05-15"),
            ("Aspirin 81mg daily", "2025-05-15"),
            ("Clopidogrel 75mg daily (12-month DES course)", "2025-05-15"),
        ],
        "allergies": [
            ("Lisinopril", "2024-08-02"),
        ],
        "labs": [
            ("2025-11-01", [
                ("42637-9", "BNP",              "890",  "pg/mL",  "0-100",   "high"),
                ("2160-0",  "Creatinine",       "1.3",  "mg/dL",  "0.5-1.1", "high"),
                ("2823-3",  "Potassium",        "4.8",  "mEq/L",  "3.5-5.0", ""),
                ("10839-9", "Troponin I",       "0.02", "ng/mL",  "0-0.04",  ""),
                ("2089-1",  "LDL Cholesterol",  "62",   "mg/dL",  "<70",     ""),
            ]),
        ],
        "encounters": [
            ("2025-11-03 09:00:00", "Post-MI HFrEF management, ICD check, and antiplatelet review"),
        ],
    },
]


# ---------------------------------------------------------------------------
# SQL generation
# ---------------------------------------------------------------------------

def _escape(s: str) -> str:
    """Escape single quotes using standard SQL double-quote style.

    Uses '' (two single quotes) rather than \\' to avoid triggering
    mariadb CLI's backslash command interpretation when SQL is piped
    via stdin.
    """
    return s.replace("'", "''")


def build_sql(patients: list[dict]) -> list[str]:
    stmts: list[str] = []

    # ── Phase 1: Wipe ───────────────────────────────────────────────────
    stmts.append("-- Phase 1: Wipe all clinical data for seed patients (pid >= 4)")
    stmts.append("DELETE FROM `lists` WHERE `pid` >= 4;")
    stmts.append(
        "DELETE pr FROM `procedure_result` pr"
        " INNER JOIN `procedure_report` prr ON pr.`procedure_report_id` = prr.`procedure_report_id`"
        " INNER JOIN `procedure_order` po ON prr.`procedure_order_id` = po.`procedure_order_id`"
        " WHERE po.`patient_id` >= 4;"
    )
    stmts.append(
        "DELETE prr FROM `procedure_report` prr"
        " INNER JOIN `procedure_order` po ON prr.`procedure_order_id` = po.`procedure_order_id`"
        " WHERE po.`patient_id` >= 4;"
    )
    # Delete procedure_order_code before procedure_order (no FK cascade)
    stmts.append(
        "DELETE poc FROM `procedure_order_code` poc"
        " INNER JOIN `procedure_order` po ON poc.`procedure_order_id` = po.`procedure_order_id`"
        " WHERE po.`patient_id` >= 4;"
    )
    stmts.append("DELETE FROM `procedure_order` WHERE `patient_id` >= 4;")
    stmts.append("DELETE FROM `forms` WHERE `pid` >= 4;")
    stmts.append("DELETE FROM `form_encounter` WHERE `pid` >= 4;")
    # Delete uuid_registry entries BEFORE deleting patient_data (need uuid for the join)
    stmts.append(
        "DELETE ur FROM `uuid_registry` ur"
        " INNER JOIN `patient_data` pd ON ur.`uuid` = pd.`uuid`"
        " WHERE pd.`pid` >= 4;"
    )
    stmts.append("DELETE FROM `patient_data` WHERE `pid` >= 4;")
    stmts.append("ALTER TABLE `patient_data` AUTO_INCREMENT = 4;")

    # ── Phase 2: Insert patients, clinical data, labs, encounters ───────
    stmts.append("\n-- Phase 2: Insert 15 synthetic patients")

    for pat in patients:
        pid = pat["pid"]
        fname = _escape(pat["fname"])
        lname = _escape(pat["lname"])
        stmts.append(f"\n-- {fname} {lname} (PID {pid})")

        # Patient demographics — include uuid so FHIR can identify this patient.
        # Without uuid, patient_data.uuid = NULL and the FHIR /Observation join
        # (which matches on patient.uuid) returns zero results.
        puuid = _uid()
        stmts.append(
            f"INSERT INTO `patient_data` (`pid`,`fname`,`lname`,`DOB`,`sex`,"
            f"`street`,`city`,`state`,`postal_code`,`phone_home`,`ss`,"
            f"`status`,`email`,`race`,`ethnicity`,`uuid`) VALUES ("
            f"{pid},'{fname}','{lname}','{pat['dob']}','{pat['sex']}',"
            f"'{_escape(pat['street'])}','{_escape(pat['city'])}','{pat['state']}',"
            f"'{pat['postal_code']}','{pat['phone_home']}','{pat['ss']}',"
            f"'active','{_escape(pat['email'])}','{pat['race']}','{pat['ethnicity']}',"
            f"UNHEX('{puuid}')"
            f");"
        )
        # Register the UUID in uuid_registry (mirrors what OpenEMR's UuidRegistry does)
        stmts.append(
            f"INSERT INTO `uuid_registry` (`uuid`,`table_name`,`table_id`,`created`)"
            f" VALUES (UNHEX('{puuid}'),'patient_data','id',NOW());"
        )

        # Conditions (medical_problem)
        base_id = 90000 + pid * 100
        for i, (title, icd10, begdate) in enumerate(pat["conditions"]):
            lid = base_id + i
            uid = _uid()
            stmts.append(
                f"INSERT INTO `lists` (`id`,`pid`,`type`,`title`,`diagnosis`,"
                f"`begdate`,`activity`,`uuid`) VALUES ("
                f"{lid},{pid},'medical_problem','{_escape(title)}',"
                f"'ICD10:{icd10}','{begdate}',1,UNHEX('{uid}'));"
            )

        # Medications (type='medication')
        med_base = base_id + 30
        for i, (title, begdate) in enumerate(pat["medications"]):
            lid = med_base + i
            uid = _uid()
            stmts.append(
                f"INSERT INTO `lists` (`id`,`pid`,`type`,`title`,`begdate`,"
                f"`activity`,`uuid`) VALUES ("
                f"{lid},{pid},'medication','{_escape(title)}','{begdate}',1,"
                f"UNHEX('{uid}'));"
            )

        # Allergies (type='allergy')
        allergy_base = base_id + 60
        for i, (title, begdate) in enumerate(pat["allergies"]):
            lid = allergy_base + i
            uid = _uid()
            stmts.append(
                f"INSERT INTO `lists` (`id`,`pid`,`type`,`title`,`begdate`,"
                f"`activity`,`uuid`) VALUES ("
                f"{lid},{pid},'allergy','{_escape(title)}','{begdate}',1,"
                f"UNHEX('{uid}'));"
            )

        # Labs
        order_base_id = 1000 + pid * 10
        result_base_id = 10000 + pid * 100
        result_counter = 0
        for j, (order_date, results) in enumerate(pat["labs"]):
            oid = order_base_id + j
            stmts.append(
                f"INSERT INTO `procedure_order` (`procedure_order_id`,`patient_id`,"
                f"`date_ordered`,`order_status`) VALUES ({oid},{pid},'{order_date}','complete');"
            )
            # procedure_order_code is required for FHIR /Observation to return results.
            # The join chain: procedure_order → procedure_order_code → procedure_report
            # (matched on procedure_order_seq) → procedure_result.
            # Use the first result's code/name as the panel code (seq=1 matches report).
            first_code, first_text = results[0][0], results[0][1]
            stmts.append(
                f"INSERT INTO `procedure_order_code` (`procedure_order_id`,"
                f"`procedure_order_seq`,`procedure_code`,`procedure_name`,`procedure_source`)"
                f" VALUES ({oid},1,'{_escape(first_code)}','{_escape(first_text)}','1');"
            )
            stmts.append(
                f"INSERT INTO `procedure_report` (`procedure_report_id`,"
                f"`procedure_order_id`,`date_report`,`report_status`) VALUES "
                f"({oid},{oid},'{order_date}','final');"
            )
            for result_code, result_text, value, units, ref_range, abnormal in results:
                rid = result_base_id + result_counter
                result_counter += 1
                stmts.append(
                    f"INSERT INTO `procedure_result` (`procedure_result_id`,"
                    f"`procedure_report_id`,`result_code`,`result_text`,`result`,"
                    f"`units`,`range`,`abnormal`,`result_status`) VALUES ("
                    f"{rid},{oid},'{_escape(result_code)}','{_escape(result_text)}',"
                    f"'{_escape(value)}','{_escape(units)}','{_escape(ref_range)}',"
                    f"'{abnormal}','final');"
                )

        # Encounters
        enc_base = 80000 + pid * 10
        for k, (enc_date, reason) in enumerate(pat["encounters"]):
            eid = enc_base + k
            stmts.append(
                f"INSERT INTO `form_encounter` (`id`,`pid`,`date`,`reason`,"
                f"`facility`,`facility_id`,`onset_date`,`pc_catid`,`billing_facility`)"
                f" VALUES ({eid},{pid},'{enc_date}','{_escape(reason)}',"
                f"'Family Medicine Clinic',3,'{enc_date[:10]}',5,3);"
            )
            stmts.append(
                f"INSERT INTO `forms` (`id`,`encounter`,`form_id`,`form_name`,"
                f"`formdir`,`pid`,`date`) VALUES ({eid},{eid},{eid},"
                f"'New Patient Encounter','newpatient',{pid},'{enc_date}');"
            )

    # ── Phase 3: Module registration ────────────────────────────────────
    stmts.append("\n-- Phase 3: Ensure ClinicalAssistant module is registered")
    stmts.append(
        "INSERT INTO `modules` (`mod_name`,`mod_directory`,`mod_parent`,`mod_type`,"
        "`mod_active`,`mod_ui_name`,`mod_relative_link`,`mod_ui_order`,`mod_ui_active`,"
        "`mod_description`,`mod_nick_name`,`mod_enc_menu`,`directory`,`date`,"
        "`sql_run`,`type`,`sql_version`,`acl_version`) VALUES ("
        "'ClinicalAssistant','oe-module-clinical-assistant','','',"
        "1,'Clinical Assistant','',"
        "0,1,'Clinical Assistant Sidebar',"
        "'','no','',NOW(),"
        "0,0,'',''"
        ") ON DUPLICATE KEY UPDATE `mod_active`=1, `mod_ui_active`=1;"
    )

    return stmts


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def run_sql_batch(stmts: list[str], dry_run: bool = False) -> bool:
    """Join all statements and pipe them to mariadb via stdin.

    Uses `docker exec -i` + stdin to avoid shell quoting issues with large
    SQL payloads. The `-e` approach breaks when SQL contains many statements
    or special characters that confuse shell interpolation.
    """
    sql_stmts = [s for s in stmts if not s.strip().startswith("--") and s.strip()]
    full_sql = "\n".join(sql_stmts) + "\n"

    if dry_run:
        print(full_sql)
        return True

    cmd = [
        "docker", "exec", "-i", DOCKER_CONTAINER,
        "mariadb",
        f"-u{DB_USER}",
        f"--password={DB_PASS}",
        "--skip-ssl",
        DB_NAME,
    ]
    try:
        result = subprocess.run(
            cmd,
            input=full_sql,
            capture_output=True,
            text=True,
            timeout=90,
        )
        if result.returncode != 0:
            stderr = result.stderr.strip()
            real_errors = [
                line for line in stderr.splitlines()
                if "password" not in line.lower()
            ]
            if real_errors:
                print(f"  ✗ SQL error:\n    " + "\n    ".join(real_errors), file=sys.stderr)
                return False
        return True
    except subprocess.TimeoutExpired:
        print("  ✗ Docker exec timed out (>90s)", file=sys.stderr)
        return False
    except Exception as exc:
        print(f"  ✗ Docker exec failed: {exc}", file=sys.stderr)
        return False


def verify(dry_run: bool = False) -> None:
    """Quick sanity-check: list inserted patients by PID."""
    if dry_run:
        return
    sql = "SELECT pid, fname, lname, DOB FROM patient_data WHERE pid >= 4 ORDER BY pid;\n"
    cmd = [
        "docker", "exec", "-i", DOCKER_CONTAINER,
        "mariadb", f"-u{DB_USER}", f"--password={DB_PASS}", "--skip-ssl", DB_NAME,
    ]
    result = subprocess.run(cmd, input=sql, capture_output=True, text=True, timeout=15)
    if result.returncode == 0:
        lines = [l for l in result.stdout.splitlines() if l.strip()]
        for line in lines:
            print(f"  {line}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Reset OpenEMR patient data to baseline")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print SQL to stdout instead of executing",
    )
    parser.add_argument(
        "--container", default=None,
        help="Docker container name (overrides OPENEMR_CONTAINER env / default)",
    )
    args = parser.parse_args()

    global DOCKER_CONTAINER
    if args.container:
        DOCKER_CONTAINER = args.container

    if args.dry_run:
        print("-- reset_patients.py dry-run output")
        print(f"-- Target container: {DOCKER_CONTAINER}")
        print(f"-- Patients: {len(PATIENTS)}")
        print()

    print("=" * 64)
    print("OpenEMR Patient Reset Script")
    if args.dry_run:
        print("MODE: dry-run (printing SQL only)")
    else:
        print(f"Container: {DOCKER_CONTAINER}")
        print(f"Database:  {DB_NAME}")
    print(f"Patients:  {len(PATIENTS)} (PIDs 4–18)")
    print("=" * 64)

    # Build all SQL upfront
    stmts = build_sql(PATIENTS)

    # Split into wipe phase (first 8 statements) and insert phase (rest),
    # then execute the entire batch in one shot for speed.
    print("\nGenerating SQL ... ", end="", flush=True)
    print(f"done ({len(stmts)} statements)")

    print("Executing reset ", end="", flush=True)
    if not args.dry_run:
        print("(this may take ~10s) ...", end="", flush=True)

    ok = run_sql_batch(stmts, dry_run=args.dry_run)

    if not args.dry_run:
        if ok:
            print(" done")
        else:
            print(" FAILED")
            sys.exit(1)

    if not args.dry_run:
        print("\nVerifying inserted patients:")
        verify(dry_run=args.dry_run)

    print()
    print("=" * 64)
    print(f"Reset {'complete' if ok else 'FAILED'}.")
    if ok and not args.dry_run:
        print("  PIDs 4-6  : eval test patients (Maria Santos, James Kowalski, Aisha Patel)")
        print("  PIDs 7-13 : extended clinical scenarios")
        print("  PIDs 14-18: oncology, hepatology, neurology, infectious disease, cardiology")
        print()
        print("To restore again at any time:")
        print("  uv run python scripts/reset_patients.py")
    print("=" * 64)


if __name__ == "__main__":
    main()
