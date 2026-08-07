# xray-s3-file-recorder

S3 `xray/` Prefix 아래에서 발생하는 `ObjectCreated` 이벤트를 처리하고, Object metadata를 읽어 PostgreSQL `public.s3_file`에 idempotent하게 기록하는 Lambda입니다.

이 README는 **Lambda 모듈 자체의 배포·설정·장애 대응**만 다룹니다.
X-RAY 전체 Spring/FastAPI/FE 통합 테스트는 `docs/xray/README.md`를 참고합니다.

---

## 1. 처리 흐름

```text
S3 ObjectCreated
      ↓
Lambda 실행
      ↓
S3 Key 파싱
      ↓
head_object로 metadata 조회
      ↓
usage / source_order / original_name 추출
      ↓
AWS Private RDS public.s3_file UPSERT
```

Lambda는 입력 파일뿐 아니라 X-RAY Workflow 과정에서 생성되는 출력 파일도 동일한 S3 Key 규칙에 따라 기록합니다.

---

## 2. 배포 ZIP 생성

Linux, macOS 또는 Git Bash에서 실행합니다.

```bash
./build_zip.sh
```

생성:

```text
dist/xray-s3-file-recorder.zip
```

ZIP에는 Python 3.11 / Linux x86_64 호환 `psycopg2`와 Lambda 소스가 포함됩니다.
빌드 시 PyPI 접근이 필요합니다.

---

## 3. Lambda 기본 설정

```text
Runtime      : Python 3.11
Architecture : x86_64
Handler      : lambda_function.lambda_handler
Trigger      : S3 ObjectCreated
Prefix       : xray/
```

필수 환경변수:

```dotenv
DB_HOST=<RDS endpoint>
DB_USER=conservation
DB_PASSWORD=<보안값>
```

선택 환경변수:

```dotenv
DB_PORT=5432
DB_NAME=conservation
DB_SCHEMA=public
S3_FILE_TABLE=s3_file
DB_SSLMODE=prefer
```

비밀번호와 실제 Secret 값은 GitHub/README/PR에 기록하지 않습니다.

---

## 4. Network / Security Group

Lambda는 RDS와 통신 가능한 VPC/Subnet에 연결되어야 합니다.

권장:

```text
Lambda SG
   ↓ TCP 5432
RDS SG
```

RDS Security Group은 Lambda Security Group에서 들어오는 TCP 5432만 허용합니다.

---

## 5. S3 metadata 계약

Lambda는 `head_object`로 사용자 metadata를 읽습니다.

입력 이미지에서 사용하는 metadata:

```text
usage
source_order
original_name
```

예시:

```text
usage=xray_original
source_order=0
original_name=piece-01.png
```

컬러 이미지 예시:

```text
usage=color_reference
original_name=color-reference.jpg
```

최신 FE는 Spring `/api/xray/stitch/jobs/prepare` 응답에서 받은 Presigned URL과 서명 대상 metadata header를 그대로 S3 PUT 요청에 사용해야 합니다.

> 실제 응답에서는 헤더 필드명이 `requiredHeaders`로 내려올 수 있습니다. 과거 코드/문서에서 `uploadHeaders`라는 이름을 사용한 경우가 있으므로, 실제 `/prepare` 응답을 기준으로 사용합니다.

기존 multipart 호환 API를 사용하는 경우 Spring이 동일 metadata를 넣어 S3에 업로드합니다.

---

## 6. 주요 usage_name

Lambda가 S3 Key / metadata를 기반으로 저장하는 대표 usage:

| usage_name              | 실제 파일                   |
| ----------------------- | --------------------------- |
| `color_reference`       | `inputs/color/*`            |
| `xray_original`         | `inputs/xray/*`             |
| `assembled_auto`        | `assembled_xray.png`        |
| `layout_auto`           | `layout.json`               |
| `layout_fragment_masks` | `layout_fragment_masks.zip` |
| `layout_final`          | `layout.final.json`         |
| `assembled_final`       | `assembled_xray.final.png`  |
| `source_owner`          | `source_owner.final.png`    |
| `fragment_owner`        | `fragment_owner.final.png`  |
| `seam_zone`             | `seam_zone.final.png`       |
| `overlap_mask`          | `overlap_mask.final.png`    |
| `provenance`            | `provenance.final.json`     |
| `defect_result`         | `defect_result.png`         |
| `report_json`           | `report.json`               |

`xray_original`은 `source_order`를 사용합니다.

---

## 7. Lambda IAM 권한

S3 Trigger가 Lambda를 호출할 수 있는 권한과 Lambda가 S3 Object metadata를 읽을 수 있는 권한은 서로 다릅니다.

`head_object` 호출을 위해 Lambda 실행 Role에 대상 S3 Object의 `s3:GetObject` 권한이 필요합니다.

예시:

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

버킷 또는 Prefix가 변경되면 Resource ARN도 함께 수정합니다.

---

## 8. 정상 동작 확인

AWS Console:

```text
Lambda
→ xray-s3-file-recorder
→ Monitor
→ View CloudWatch logs
→ 최신 Log Stream
```

확인 항목:

```text
processed 증가
errors = []
artifactId 정상 파싱
usage 정상 파싱
source_order 정상 파싱
DB 연결 성공
```

AWS RDS 확인:

```sql
SELECT
    artifact_id,
    usage_name,
    source_order,
    original_name,
    s3_key,
    status,
    created_at
FROM public.s3_file
WHERE artifact_id = '<artifactId>'
ORDER BY usage_name, source_order NULLS FIRST;
```

입력 업로드 직후 예상:

```text
color_reference / source_order NULL
xray_original / source_order 0
xray_original / source_order 1
```

---

## 9. 장애 대응

### Lambda가 호출되지 않음

확인:

```text
S3 Trigger 존재 여부
Event = ObjectCreated
Prefix = xray/
Lambda Resource-based policy
```

### Lambda는 호출되지만 metadata를 읽지 못함

확인:

```text
Lambda Role에 s3:GetObject 권한 존재
대상 Resource ARN에 xray/* 포함
Object Key가 올바른 Bucket에 존재
```

### RDS 연결 실패

확인 순서:

```text
Lambda VPC/Subnet
→ Lambda SG
→ RDS SG 5432 Source
→ DB_HOST / DB_USER / DB_PASSWORD
→ RDS 상태
```

### s3_file에 기록되지 않음

CloudWatch에서 다음을 확인합니다.

```text
S3 Key 파싱 실패
usage_name 매핑 실패
DB UPSERT 오류
중복 Key 처리
```

---

## 10. 로컬 Python 테스트

**PC PowerShell — BE 프로젝트 폴더에서 입력**

```powershell
Push-Location .\aws\lambda\xray-s3-file-recorder
python -m unittest discover -s tests -v
Pop-Location
```

주요 테스트 대상:

```text
S3 Key parsing
usage_name mapping
source_order parsing
```

---

## 11. 관련 문서

X-RAY 전체 구조와 실제 팀원 E2E 테스트 절차:

```text
docs/xray/README.md
```

전체 Workflow:

```text
FE
→ Spring /prepare
→ Presigned S3 PUT
→ S3 ObjectCreated
→ Lambda
→ AWS RDS s3_file
→ Spring /start
→ FastAPI Stitch
→ Finalization
→ Defect Detect / Review
→ AI Report
→ Complete
```
