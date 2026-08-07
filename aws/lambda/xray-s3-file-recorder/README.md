# xray-s3-file-recorder

S3 `ObjectCreated` events under `xray/` are parsed and idempotently recorded in PostgreSQL `public.s3_file`.

## 배포 ZIP 생성

Linux, macOS 또는 Git Bash에서 실행한다.

```bash
./build_zip.sh
```

`dist/xray-s3-file-recorder.zip`에 Python 3.11 / Linux x86_64 호환 `psycopg2`와 Lambda 소스가 생성된다. 빌드 시 PyPI 접근이 필요하다.

## Lambda 설정

- Runtime: Python 3.11
- Architecture: x86_64
- Handler: `lambda_function.lambda_handler`
- Trigger: S3 ObjectCreated, prefix `xray/`

필수 환경변수: `DB_HOST`, `DB_USER`, `DB_PASSWORD`

선택 환경변수: `DB_PORT=5432`, `DB_NAME=conservation`, `DB_SCHEMA=public`, `S3_FILE_TABLE=s3_file`, `DB_SSLMODE=prefer`.

RDS 보안그룹은 Lambda 보안그룹에서 들어오는 TCP 5432만 허용하고, Lambda는 RDS와 통신 가능한 private subnet에 연결한다.
