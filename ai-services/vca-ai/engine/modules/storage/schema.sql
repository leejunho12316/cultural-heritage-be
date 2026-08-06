CREATE TABLE artifact (
    id uuid PRIMARY KEY,
    artifact_code text UNIQUE,
    title text,
    description text,
    representative_image_key text,
    metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE assessment_run (
    id uuid PRIMARY KEY,
    artifact_id uuid NOT NULL REFERENCES artifact(id),
    run_number integer NOT NULL,
    legacy_project_name text,
    status text NOT NULL,
    dry_run boolean NOT NULL,
    requested_device text,
    resolved_device text,
    current_stage text,
    progress_percent integer NOT NULL,
    started_at timestamptz,
    completed_at timestamptz,
    config_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (artifact_id, run_number)
);

CREATE TABLE uploaded_image (
    id uuid PRIMARY KEY,
    artifact_id uuid NOT NULL REFERENCES artifact(id),
    filename text NOT NULL,
    object_key text NOT NULL,
    thumbnail_object_key text,
    media_type text,
    content_sha256 text,
    size_bytes bigint,
    status text NOT NULL,
    width integer,
    height integer,
    display_order integer NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    uploaded_at timestamptz,
    UNIQUE (artifact_id, object_key)
);

CREATE TABLE assessment_report (
    assessment_run_id uuid PRIMARY KEY REFERENCES assessment_run(id),
    report_json jsonb NOT NULL,
    status text,
    overall_condition text,
    risk_level text,
    generated_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE report_pdf_job (
    id uuid PRIMARY KEY,
    assessment_run_id uuid NOT NULL REFERENCES assessment_run(id),
    status text NOT NULL,
    layout text NOT NULL,
    pdf_object_key text,
    requested_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz
);
