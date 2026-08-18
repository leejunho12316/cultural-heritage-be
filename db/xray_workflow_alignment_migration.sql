-- X-RAY Notion alignment migration
-- Target: existing S3/RDS integration schema -> XRAY_JOB + XRAY_DEFECT 2-table workflow
--
-- IMPORTANT
-- 1) Take an RDS snapshot/backup before running.
-- 2) Run the duplicate check first. artifact_id must be unique for the 1:0..1 rule.
-- 3) ARTIFACT/USER foreign keys are intentionally deferred until the common APIs are integrated.

BEGIN;

-- -------------------------------------------------------------------------
-- 1. Pre-check: one X-ray job per artifact
-- -------------------------------------------------------------------------
DO $$
DECLARE
    duplicate_count bigint;
BEGIN
    SELECT COUNT(*)
      INTO duplicate_count
      FROM (
          SELECT artifact_id
            FROM public.xray_job
           GROUP BY artifact_id
          HAVING COUNT(*) > 1
      ) duplicates;

    IF duplicate_count > 0 THEN
        RAISE EXCEPTION
            'xray_job contains % duplicated artifact_id value(s). Resolve duplicates before migration.',
            duplicate_count;
    END IF;
END $$;

-- -------------------------------------------------------------------------
-- 2. XRAY_JOB columns
-- -------------------------------------------------------------------------
ALTER TABLE public.xray_job
    ADD COLUMN IF NOT EXISTS user_id uuid,
    ADD COLUMN IF NOT EXISTS report_text text,
    ADD COLUMN IF NOT EXISTS expected_color_count int NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS expected_xray_count int NOT NULL DEFAULT 0;

-- Preserve expected counts from the previous JSON input list when available.
UPDATE public.xray_job
   SET expected_xray_count = CASE
       WHEN xray_file_names IS NULL THEN expected_xray_count
       WHEN jsonb_typeof(xray_file_names) = 'array' THEN jsonb_array_length(xray_file_names)
       ELSE expected_xray_count
   END
 WHERE EXISTS (
     SELECT 1
       FROM information_schema.columns
      WHERE table_schema = 'public'
        AND table_name = 'xray_job'
        AND column_name = 'xray_file_names'
 );

-- Old status meant the stitching sub-job state. Convert it to the new whole-X-ray workflow state.
UPDATE public.xray_job
   SET status = CASE status
       WHEN 'PENDING' THEN 'PREPARED'
       WHEN 'RUNNING' THEN 'STITCHING'
       WHEN 'COMPLETED' THEN 'STITCHED'
       WHEN 'FINALIZING' THEN 'STITCHED'
       WHEN 'FINALIZED' THEN 'STITCHED'
       WHEN 'FAILED' THEN 'FAILED'
       ELSE status
   END;

-- Old completed_at represented stitching/finalizer completion, not whole X-ray completion.
UPDATE public.xray_job
   SET completed_at = NULL
 WHERE status <> 'COMPLETED';

-- Remove old status check constraints, regardless of their generated name.
DO $$
DECLARE
    constraint_row record;
BEGIN
    FOR constraint_row IN
        SELECT conname
          FROM pg_constraint
         WHERE conrelid = 'public.xray_job'::regclass
           AND contype = 'c'
           AND pg_get_constraintdef(oid) ILIKE '%status%'
    LOOP
        EXECUTE format(
            'ALTER TABLE public.xray_job DROP CONSTRAINT %I',
            constraint_row.conname
        );
    END LOOP;
END $$;

ALTER TABLE public.xray_job
    ADD CONSTRAINT ck_xray_job_status CHECK (
        status IN (
            'PREPARED',
            'UPLOADING',
            'STITCHING',
            'STITCHED',
            'DETECTING',
            'DETECTING_FRAGMENTS',
            'DETECTING_ASSEMBLED',
            'MAPPING',
            'REVIEW_READY',
            'REPORT_READY',
            'REPORTING',
            'COMPLETED',
            'FAILED'
        )
    );

CREATE UNIQUE INDEX IF NOT EXISTS uk_xray_job_artifact
    ON public.xray_job (artifact_id);

-- The old columns are intentionally retained for one deployment as rollback data:
-- message, color_file_name, xray_file_names, finalized_at.
-- They are no longer read by the application and can be dropped in a later cleanup migration.

-- -------------------------------------------------------------------------
-- 3. XRAY_DEFECT
-- -------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.xray_defect (
    id bigserial PRIMARY KEY,
    xray_job_id uuid NOT NULL,
    origin_type varchar(20) NOT NULL,
    geometry jsonb NOT NULL,
    review_decision varchar(20) NOT NULL DEFAULT 'DAMAGE',
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_xray_defect_job
        FOREIGN KEY (xray_job_id)
        REFERENCES public.xray_job(id)
        ON DELETE CASCADE,
    CONSTRAINT ck_xray_defect_origin_type CHECK (
        origin_type IN ('MATCHED', 'SOURCE_ONLY', 'ASSEMBLED_ONLY')
    ),
    CONSTRAINT ck_xray_defect_review_decision CHECK (
        review_decision IN ('DAMAGE', 'NORMAL')
    )
);

CREATE INDEX IF NOT EXISTS idx_xray_defect_job
    ON public.xray_defect (xray_job_id, id);

-- -------------------------------------------------------------------------
-- 4. Shared S3_FILE additions and canonical usage migration
-- -------------------------------------------------------------------------
ALTER TABLE public.s3_file
    ADD COLUMN IF NOT EXISTS original_name varchar(500);

UPDATE public.s3_file
   SET original_name = regexp_replace(s3_key, '^.*/', '')
 WHERE original_name IS NULL;

UPDATE public.s3_file
   SET usage_name = CASE usage_name
       WHEN 'XRAY_ORIGINAL' THEN 'xray_original'
       WHEN 'COLOR_ORIGINAL' THEN 'color_reference'
       WHEN 'ASSEMBLED' THEN 'assembled_auto'
       WHEN 'LAYOUT' THEN 'layout_auto'
       WHEN 'STITCH_REPORT' THEN 'report_json'
       WHEN 'FINALIZATION_BUNDLE' THEN 'legacy_finalization_bundle'
       WHEN 'FINAL_LAYOUT' THEN 'layout_final'
       WHEN 'FINAL_ASSEMBLED' THEN 'assembled_final'
       WHEN 'SOURCE_OWNER' THEN 'source_owner'
       WHEN 'FRAGMENT_OWNER' THEN 'fragment_owner'
       WHEN 'SEAM_ZONE' THEN 'seam_zone'
       WHEN 'OVERLAP_MASK' THEN 'overlap_mask'
       WHEN 'PROVENANCE' THEN 'provenance'
       ELSE usage_name
   END
 WHERE module_type = 'XRAY';

CREATE INDEX IF NOT EXISTS idx_s3_file_artifact_usage_order
    ON public.s3_file (artifact_id, module_type, usage_name, source_order);

COMMIT;

-- Future common-schema integration (do not run yet):
-- ALTER TABLE public.xray_job
--     ADD CONSTRAINT fk_xray_job_artifact
--     FOREIGN KEY (artifact_id) REFERENCES public.artifact(id);
-- ALTER TABLE public.xray_job
--     ADD CONSTRAINT fk_xray_job_user
--     FOREIGN KEY (user_id) REFERENCES public.users(id);
