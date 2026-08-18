-- assessment_report.overall_condition was created as the default JPA String
-- column length (varchar(255)), sized for a short risk classification that
-- was never implemented. VcaOverallConditionGenerator now stores a full
-- LLM-generated paragraph there (no sentence-count cap by design), which
-- routinely exceeds 255 characters and fails the INSERT with
-- "value too long for type character varying(255)".
ALTER TABLE assessment_report
    ALTER COLUMN overall_condition TYPE text;
