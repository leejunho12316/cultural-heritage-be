-- Artifact ownership migration
-- Existing artifacts are assigned to an ADMIN account first (fallback: lowest user id).
-- New artifacts are populated from the authenticated user by ArtifactService.

ALTER TABLE artifacts
    ADD COLUMN IF NOT EXISTS user_id BIGINT;

DO $$
DECLARE
    v_owner_id BIGINT;
BEGIN
    SELECT id
      INTO v_owner_id
      FROM users
     ORDER BY CASE WHEN role = 'ADMIN' THEN 0 ELSE 1 END, id
     LIMIT 1;

    IF v_owner_id IS NULL THEN
        RAISE EXCEPTION 'artifact owner migration requires at least one users row';
    END IF;

    UPDATE artifacts
       SET user_id = v_owner_id
     WHERE user_id IS NULL;
END $$;

ALTER TABLE artifacts
    ALTER COLUMN user_id SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conname = 'fk_artifacts_user_id'
    ) THEN
        ALTER TABLE artifacts
            ADD CONSTRAINT fk_artifacts_user_id
            FOREIGN KEY (user_id) REFERENCES users(id);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_artifacts_user_id
    ON artifacts(user_id);
