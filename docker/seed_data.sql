-- Synthetic seed data for OpenEMR clinical agent testing.
-- OpenEMR auto-creates its schema on first boot; this file runs after
-- schema init (loaded as 99-seed.sql) and inserts fictional patients
-- with basic clinical data.

-- Wait for OpenEMR to create its tables. If patient_data doesn't exist
-- yet, this script will error and MySQL will log it — that's expected;
-- OpenEMR's own init may run these inserts later via the FHIR API
-- seeding path instead.

USE openemr;

-- ============================================================
-- Patients
-- ============================================================

INSERT INTO `patient_data` (
  `pid`, `fname`, `lname`, `DOB`, `sex`, `street`,
  `city`, `state`, `postal_code`, `phone_home`, `ss`,
  `status`, `email`, `race`, `ethnicity`
) VALUES
(1, 'Maria', 'Santos', '1985-03-14', 'Female',
 '742 Evergreen Terrace', 'Springfield', 'IL', '62704',
 '217-555-0142', '123-45-6789', 'active',
 'maria.santos@example.com', 'white', 'hisp_or_latin'),

(2, 'James', 'Kowalski', '1958-11-02', 'Male',
 '310 Oak Lane', 'Madison', 'WI', '53703',
 '608-555-0198', '987-65-4321', 'active',
 'j.kowalski@example.com', 'white', 'not_hisp_or_latin'),

(3, 'Aisha', 'Patel', '1972-07-28', 'Female',
 '88 Birch Street', 'Austin', 'TX', '78701',
 '512-555-0167', '456-78-9012', 'active',
 'aisha.patel@example.com', 'asian', 'not_hisp_or_latin')
ON DUPLICATE KEY UPDATE `fname` = VALUES(`fname`);


-- ============================================================
-- Diagnoses / Problem List  (lists + lists_touch)
-- OpenEMR stores problems in the `lists` table with type='medical_problem'.
-- ============================================================

-- Use high IDs (90xxx) to avoid collision with Synthea demo data.
INSERT INTO `lists` (
  `id`, `pid`, `type`, `title`, `diagnosis`, `begdate`, `activity`
) VALUES
-- Maria Santos (pid=4)
(90101, 4, 'medical_problem', 'Type 2 Diabetes Mellitus', 'ICD10:E11.9', '2019-06-15', 1),
(90102, 4, 'medical_problem', 'Essential Hypertension',   'ICD10:I10',   '2020-01-20', 1),

-- James Kowalski (pid=5)
(90201, 5, 'medical_problem', 'Chronic Obstructive Pulmonary Disease', 'ICD10:J44.1', '2015-09-10', 1),
(90202, 5, 'medical_problem', 'Atrial Fibrillation',                   'ICD10:I48.91', '2021-03-05', 1),
(90203, 5, 'medical_problem', 'Type 2 Diabetes Mellitus',              'ICD10:E11.65', '2018-11-12', 1),

-- Aisha Patel (pid=6)
(90301, 6, 'medical_problem', 'Major Depressive Disorder, recurrent', 'ICD10:F33.1', '2017-04-22', 1),
(90302, 6, 'medical_problem', 'Hypothyroidism',                       'ICD10:E03.9', '2020-08-01', 1)
ON DUPLICATE KEY UPDATE `title` = VALUES(`title`);


-- ============================================================
-- Medications (stored as type='medication' in `lists`)
-- ============================================================

INSERT INTO `lists` (
  `id`, `pid`, `type`, `title`, `begdate`, `activity`
) VALUES
-- Maria Santos (pid=4)
(90103, 4, 'medication', 'Metformin 500mg twice daily',       '2019-06-15', 1),
(90104, 4, 'medication', 'Lisinopril 10mg daily',             '2020-01-20', 1),

-- James Kowalski (pid=5)
(90204, 5, 'medication', 'Tiotropium 18mcg inhaler daily',    '2015-09-10', 1),
(90205, 5, 'medication', 'Apixaban 5mg twice daily',          '2021-03-05', 1),
(90206, 5, 'medication', 'Metformin 1000mg twice daily',      '2018-11-12', 1),

-- Aisha Patel (pid=6)
(90303, 6, 'medication', 'Sertraline 100mg daily',            '2017-04-22', 1),
(90304, 6, 'medication', 'Levothyroxine 75mcg daily',         '2020-08-01', 1)
ON DUPLICATE KEY UPDATE `title` = VALUES(`title`);


-- ============================================================
-- Lab results (procedure_result + procedure_order + procedure_report)
-- OpenEMR lab flow: procedure_order -> procedure_report -> procedure_result
-- Simplified: insert minimal linked records.
-- ============================================================

INSERT INTO `procedure_order` (
  `procedure_order_id`, `patient_id`, `date_ordered`, `order_status`
) VALUES
(1, 4, '2025-01-10', 'complete'),
(2, 4, '2025-07-15', 'complete'),
(3, 5, '2025-02-20', 'complete'),
(4, 6, '2025-03-05', 'complete')
ON DUPLICATE KEY UPDATE `order_status` = VALUES(`order_status`);

INSERT INTO `procedure_report` (
  `procedure_report_id`, `procedure_order_id`, `date_report`, `report_status`
) VALUES
(1, 1, '2025-01-10', 'final'),
(2, 2, '2025-07-15', 'final'),
(3, 3, '2025-02-20', 'final'),
(4, 4, '2025-03-05', 'final')
ON DUPLICATE KEY UPDATE `report_status` = VALUES(`report_status`);

INSERT INTO `procedure_result` (
  `procedure_result_id`, `procedure_report_id`, `result_code`, `result_text`,
  `result`, `units`, `range`, `abnormal`, `result_status`
) VALUES
-- Maria Santos: HbA1c trending
(1, 1, '4548-4', 'Hemoglobin A1c', '7.8', '%', '4.0-5.6', 'high', 'final'),
(2, 2, 'Hemoglobin A1c', 'Hemoglobin A1c', '8.2', '%', '4.0-5.6', 'high', 'final'),

-- James Kowalski: BNP (heart failure marker)
(3, 3, '42637-9', 'BNP', '385', 'pg/mL', '0-100', 'high', 'final'),

-- Aisha Patel: TSH
(4, 4, '11579-0', 'TSH', '6.8', 'mIU/L', '0.4-4.0', 'high', 'final')
ON DUPLICATE KEY UPDATE `result` = VALUES(`result`);


-- ============================================================
-- Encounters for seed patients
-- Using high IDs (90001+) to avoid conflicts with Synthea demo data
-- ============================================================

INSERT INTO `form_encounter` (
  `id`, `pid`, `date`, `reason`, `facility`, `facility_id`,
  `onset_date`, `pc_catid`, `billing_facility`
) VALUES
-- Maria Santos (pid=4)
(90001, 4, '2025-01-15 09:00:00', 'Initial diabetes follow-up', '', 0, '2025-01-15', 5, 0),
(90002, 4, '2025-07-20 10:00:00', 'Diabetes management and medication review', '', 0, '2025-07-20', 5, 0),

-- James Kowalski (pid=5)
(90003, 5, '2025-02-10 14:00:00', 'COPD exacerbation evaluation', '', 0, '2025-02-10', 5, 0),

-- Aisha Patel (pid=6)
(90004, 6, '2025-03-05 11:00:00', 'Depression follow-up and thyroid management', '', 0, '2025-03-05', 5, 0)
ON DUPLICATE KEY UPDATE `reason` = VALUES(`reason`);

-- OpenEMR also requires a forms table entry for each encounter
INSERT INTO `forms` (
  `id`, `encounter`, `form_id`, `form_name`, `formdir`, `pid`, `date`
) VALUES
(90001, 90001, 90001, 'New Patient Encounter', 'newpatient', 4, '2025-01-15 09:00:00'),
(90002, 90002, 90002, 'New Patient Encounter', 'newpatient', 4, '2025-07-20 10:00:00'),
(90003, 90003, 90003, 'New Patient Encounter', 'newpatient', 5, '2025-02-10 14:00:00'),
(90004, 90004, 90004, 'New Patient Encounter', 'newpatient', 6, '2025-03-05 11:00:00')
ON DUPLICATE KEY UPDATE `form_name` = VALUES(`form_name`);
