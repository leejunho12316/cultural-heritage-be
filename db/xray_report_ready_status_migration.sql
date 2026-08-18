-- Add REPORT_READY to the persisted X-ray workflow state machine.
-- Run this once on an existing PostgreSQL database before deploying code that writes REPORT_READY.

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
