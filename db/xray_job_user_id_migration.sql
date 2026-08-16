-- xray_job.user_id: uuid -> bigint migration
--
-- 배경: xray_job.user_id는 db/xray_s3_rds.sql/xray_workflow_alignment_migration.sql이
-- 만들 당시 실제 FK 제약을 보류한 채 uuid로 남겨뒀다("실제 FK 제약은 보류한다"
-- 주석 참고) - 그 뒤 XrayJob 엔티티가 users(id)를 가리키는 Long userId로
-- 확정되면서 타입이 어긋났고, Hibernate ddl-auto: validate가 이 컬럼에서
-- 걸린다.
--
-- 이 컬럼은 지금까지 실제로 채워진 적이 없는(FK 미보류 상태로만 존재한)
-- 컬럼이라, 기존 값을 bigint로 의미 있게 변환할 방법이 없다 - 있다면 그건
-- 애초에 users(id)를 가리키는 값이 아니었으므로 NULL로 비운다.
--
-- 이미 bigint인 환경(예: 이 마이그레이션이 이미 적용된 DB)에서 다시 실행해도
-- 안전하도록 타입을 먼저 확인한다.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name = 'xray_job'
           AND column_name = 'user_id'
           AND data_type <> 'bigint'
    ) THEN
        ALTER TABLE public.xray_job
            ALTER COLUMN user_id TYPE bigint USING NULL;
    END IF;
END $$;
