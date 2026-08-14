-- tasks.created_date / tasks.last_edited_date: varchar -> timestamptz migration
--
-- 배경: 두 컬럼이 지금은 KST 오프셋이 포함된 ISO-8601 문자열
-- ("yyyy-MM-dd'T'HH:mm:ssXXX", 예: "2026-08-14T11:53:00+09:00")로 저장돼
-- 있다. 다른 테이블(artifacts.created_at 등)과 다르게 문자열로 남겨뒀던
-- 이유는 "AI 쪽이 만든 시각 문자열을 그대로 보존"하려는 것이었지만, 실제로는
-- Spring(TaskService.nowKst())이 직접 계산한 값이라 그 이유가 적용되지
-- 않았다 - 그래서 다른 테이블과 통일해서 timestamptz로 바꾼다.
--
-- 기존 문자열이 이미 오프셋을 포함한 ISO-8601 형식이라 Postgres가
-- ::timestamptz로 직접 캐스팅할 수 있다 - 별도 파싱/백필 로직 불필요.
--
-- IMPORTANT
-- 1) RDS 스냅샷/백업 먼저 뜰 것.
-- 2) ddl-auto: validate 전환된 상태이므로, 이 스크립트가 배포 전에 먼저
--    반영되어야 한다 - 순서 안 맞으면 배포 후 기동 실패.

BEGIN;

ALTER TABLE public.tasks
    ALTER COLUMN created_date TYPE timestamptz USING created_date::timestamptz;

ALTER TABLE public.tasks
    ALTER COLUMN last_edited_date TYPE timestamptz USING last_edited_date::timestamptz;

COMMIT;
