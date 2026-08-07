-- Reference DDL. When Hibernate manages xray_job, do not run the xray_job
-- section independently without checking the existing schema.
CREATE TABLE IF NOT EXISTS public.xray_job (
    id uuid PRIMARY KEY,
    artifact_id uuid NOT NULL,
    status varchar(20) NOT NULL,
    message varchar(500),
    error_message text,
    color_file_name varchar(500),
    xray_file_names jsonb,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at timestamptz,
    finalized_at timestamptz
);
CREATE INDEX IF NOT EXISTS idx_xray_job_artifact_created
    ON public.xray_job (artifact_id, created_at DESC);

CREATE TABLE IF NOT EXISTS public.s3_file (
    id uuid PRIMARY KEY,
    artifact_id uuid NOT NULL,
    module_type varchar(20) NOT NULL,
    usage_name varchar(30) NOT NULL,
    source_order int,
    s3_key varchar(500) NOT NULL UNIQUE,
    bucket_name varchar(255),
    size_bytes bigint,
    etag varchar(255),
    status varchar(20) NOT NULL DEFAULT 'COMPLETED',
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);
