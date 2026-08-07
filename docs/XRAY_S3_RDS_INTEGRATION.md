# X-ray S3·Lambda·RDS 통합 구조

## 목표

최신 `main`의 결함 탐지·결함 매핑·Konva 수동 보정·육안 조사·보존 가이드·EKS 구성을 유지하면서, X-ray 결합에만 사용하던 `shared`/EFS 경로 의존성을 제거한다.

## 처리 흐름

```text
1. FE -> Spring  POST /api/xray/stitch/jobs/prepare
2. Spring -> FE  입력 파일별 presigned PUT URL
3. FE -> S3      컬러 기준 이미지와 X-ray 조각 직접 업로드
4. S3 -> Lambda  ObjectCreated 이벤트
5. Lambda -> RDS public.s3_file upsert
6. FE -> Spring  POST /api/xray/stitch/jobs/{jobId}/start
7. Spring -> FastAPI  입력 GET URL + 출력 PUT URL + callback URL
8. FastAPI       /tmp 작업공간에 내려받아 결합
9. FastAPI -> S3 assembled_xray.png, layout.json, report.json,
                  finalization_bundle.zip 업로드
10. FastAPI -> Spring callback -> RDS xray_job COMPLETED
11. FE -> Spring  PUT layout/final
12. Spring -> S3  layout.final.json 저장
13. Spring -> FastAPI  bundle GET URL + final layout GET URL + 최종 출력 PUT URL
14. FastAPI -> S3 최종 결합본 및 provenance 산출물 업로드
15. FastAPI -> Spring callback -> RDS xray_job FINALIZED
```

콜백이 일시적으로 유실되어도 S3 산출물을 기준으로 다음 API에서 상태를 복구한다.

```http
POST /api/xray/stitch/jobs/{jobId}/reconcile
```

## S3 키 규칙

```text
xray/{artifactId}/inputs/color/{fileName}
xray/{artifactId}/inputs/xray/{fileName}

xray/{artifactId}/outputs/assembled_xray.png
xray/{artifactId}/outputs/layout.json
xray/{artifactId}/outputs/report.json
xray/{artifactId}/outputs/finalization_bundle.zip
xray/{artifactId}/outputs/layout.final.json
xray/{artifactId}/outputs/assembled_xray.final.png
xray/{artifactId}/outputs/source_owner.final.png
xray/{artifactId}/outputs/fragment_owner.final.png
xray/{artifactId}/outputs/seam_zone.final.png
xray/{artifactId}/outputs/overlap_mask.final.png
xray/{artifactId}/outputs/provenance.final.json
```

현재 키는 `artifactId` 기준이므로 같은 유물로 다시 실행하면 기존 산출물을 덮어쓴다. 재작업 이력이 필요하면 `jobId`를 키에 포함하고 `ARTIFACT : XRAY_JOB`을 1:N으로 바꾸는 별도 설계가 필요하다.

## FE API 계약

### 1. 업로드 준비

```http
POST /api/xray/stitch/jobs/prepare
Content-Type: application/json
```

```json
{
  "artifactId": "UUID",
  "colorFileName": "color.png",
  "xrayFileNames": ["piece-01.png", "piece-02.png"]
}
```

응답의 `color.uploadUrl`, `xrays[].uploadUrl`로 브라우저가 S3에 `PUT`한다. 입력 URL에는 `Content-Type`을 서명하지 않으므로 브라우저 MIME 값 차이로 인한 403을 피한다.

### 2. 결합 시작

```http
POST /api/xray/stitch/jobs/{jobId}/start
Content-Type: application/json
```

```json
{
  "colorFileName": "color.png",
  "xrayFileNames": ["piece-01.png", "piece-02.png"]
}
```

`202`와 JSON 상태를 반환한다.

### 3. 상태 및 결과

```text
GET  /api/xray/stitch/jobs/{jobId}
GET  /api/xray/stitch/jobs/{jobId}/result-url
GET  /api/xray/stitch/jobs/{jobId}/layout-url
GET  /api/xray/stitch/jobs/{jobId}/report
GET  /api/xray/stitch/jobs/{jobId}/result/final-url
```

최신 main 호환을 위해 이미지 바이트와 JSON 본문을 직접 반환하는 기존 경로도 유지한다.

```text
GET /api/xray/stitch/jobs/{jobId}/result
GET /api/xray/stitch/jobs/{jobId}/layout
GET /api/xray/stitch/jobs/{jobId}/result/final
```

기존 multipart FE도 아래 경로로 계속 동작한다. 이 경우 Spring이 받은 파일을 S3에 올린 뒤 동일한 URL 기반 파이프라인을 시작한다.

```text
POST /api/xray/stitch/jobs
```

## Lambda

위치:

```text
aws/lambda/xray-s3-file-recorder/
```

- 런타임: Python 3.11, x86_64
- 핸들러: `lambda_function.lambda_handler`
- 트리거: S3 `ObjectCreated`, prefix `xray/`
- `s3_key` UNIQUE + `ON CONFLICT`로 중복 이벤트를 멱등 처리
- 멀티 레코드 이벤트는 레코드별 DB 트랜잭션으로 처리

## 데이터베이스

참고 DDL:

```text
db/xray_s3_rds.sql
```

`spring.jpa.hibernate.ddl-auto=update`를 운영에서 그대로 사용할지, Flyway/Liquibase로 전환할지는 팀 배포 정책에 맞춰 결정한다. 운영 DB에 참고 DDL을 바로 적용하기 전에 기존 스키마와 반드시 비교한다.

## 실행 환경

### Docker Compose

`shared` volume을 사용하지 않는다. X-ray FastAPI의 `/tmp/xray_jobs`, `/tmp/xray_cache`는 임시 작업공간이며 S3와 RDS가 정본이다.

### EKS

EFS/PV/PVC를 제거하고 `emptyDir`를 사용한다. 백엔드는 장기 AWS 키 대신 EKS Pod Identity 또는 IRSA 사용을 권장한다. `app-secrets`에는 최소한 다음 값이 필요하다.

```text
AWS_S3_BUCKET
SPRING_DATASOURCE_URL
SPRING_DATASOURCE_USERNAME
SPRING_DATASOURCE_PASSWORD
XRAY_STITCH_CALLBACK_TOKEN
OPENAI_API_KEY                 # 문안 생성 사용 시
```

## 실패 복구 기준

- FastAPI 처리 실패: callback으로 `FAILED` 저장
- callback만 실패: FastAPI 로컬 상태는 완료 상태 유지, Spring `/reconcile`로 S3 산출물 기준 복구
- S3 이벤트 중복: Lambda upsert로 동일 `s3_key` 갱신
- FastAPI Pod 재시작: `/tmp` 작업은 사라져도 S3/RDS 정본은 유지
- 최종화 재시도: S3의 `finalization_bundle.zip`과 `layout.final.json`으로 새 Pod에서도 렌더링 가능
