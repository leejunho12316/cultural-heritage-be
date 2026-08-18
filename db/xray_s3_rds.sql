-- X-RAY + S3/RDS reference DDL
--
-- 현재 단계에서는 ARTIFACT / USER API가 아직 통합되지 않았으므로
-- artifact_id, user_id의 실제 FK 제약은 보류한다.
-- artifact_id는 FE local/session에서 전달받는 UUID이며 유물당 X-ray 작업 1회 정책을
-- 위해 UNIQUE만 먼저 적용한다.

CREATE TABLE IF NOT EXISTS public.xray_job (
    id uuid PRIMARY KEY,
    artifact_id uuid NOT NULL UNIQUE,
    user_id uuid,
    status varchar(20) NOT NULL,
    error_message text,
    report_text text,
    expected_color_count int NOT NULL DEFAULT 1,
    expected_xray_count int NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at timestamptz,
    CONSTRAINT ck_xray_job_status CHECK (
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
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_xray_job_artifact
    ON public.xray_job (artifact_id);

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

-- 기존 공용 S3_FILE 구조에 X-ray에서 사용하는 메타데이터 컬럼을 포함한다.
CREATE TABLE IF NOT EXISTS public.s3_file (
    id uuid PRIMARY KEY,
    artifact_id uuid NOT NULL,
    module_type varchar(20) NOT NULL,
    usage_name varchar(30) NOT NULL,
    source_order int,
    original_name varchar(500),
    s3_key varchar(500) NOT NULL UNIQUE,
    bucket_name varchar(255),
    size_bytes bigint,
    etag varchar(255),
    status varchar(20) NOT NULL DEFAULT 'COMPLETED',
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE public.s3_file
    ADD COLUMN IF NOT EXISTS original_name varchar(500);

CREATE INDEX IF NOT EXISTS idx_s3_file_artifact_usage_order
    ON public.s3_file (artifact_id, module_type, usage_name, source_order);

-- ARTIFACT / USER 테이블 통합 후 적용할 FK 예시
-- ALTER TABLE public.xray_job
--     ADD CONSTRAINT fk_xray_job_artifact
--     FOREIGN KEY (artifact_id) REFERENCES public.artifact(id);
-- ALTER TABLE public.xray_job
--     ADD CONSTRAINT fk_xray_job_user
--     FOREIGN KEY (user_id) REFERENCES public.users(id);
