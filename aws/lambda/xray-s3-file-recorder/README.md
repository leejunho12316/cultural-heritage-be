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

## S3 metadata 계약

Lambda는 `head_object`로 아래 사용자 metadata를 읽는다.

- `usage`: `color_reference`, `xray_original` 등 공용 usage 이름
- `source_order`: X-RAY 원본 순서(0부터 시작)
- `original_name`: 최초 업로드 파일명

신규 FE는 `/jobs/prepare` 응답의 `uploadHeaders`를 Presigned PUT 요청에 그대로 포함해야 한다. 기존 multipart FE는 Spring이 동일 metadata를 넣는다.

## Lambda IAM 권한

S3 trigger가 Lambda 호출 권한을 부여하는 것과, Lambda가 객체 metadata를 읽는 권한은 별개다. Lambda 실행 Role에는 최소한 아래 권한이 필요하다.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject"],
      "Resource": "arn:aws:s3:::bigproject09-xray-test-428270342381/xray/*"
    }
  ]
}
```

버킷명이 바뀌면 Resource ARN도 함께 변경한다.
