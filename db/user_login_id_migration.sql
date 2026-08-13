BEGIN;

ALTER TABLE users
    ADD COLUMN login_id VARCHAR(255);

ALTER TABLE users
    ADD CONSTRAINT uk_users_login_id UNIQUE (login_id);

ALTER TABLE users
    ALTER COLUMN login_id SET NOT NULL;

COMMIT;
