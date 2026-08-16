# Troubleshooting

로컬 docker-compose로 VCA v2 스택을 처음부터 다시 띄우면서 실제로 걸렸던 문제들과 해결 방법을 정리한다. 대부분 `feature/vca_v2_be`/`feature/vca_v2_fe`가 origin/main과 병합되면서, main 쪽이 새로 추가한 JWT 인증·유물 소유권 스키마와 우리 쪽 VCA 기능이 서로 안 맞물려서 생긴 문제들이다.

## 1. `conservation-backend` 컨테이너가 기동 자체를 못 함 (JWT_SECRET)

**증상**: `docker compose up` 직후 `conservation-backend`가 바로 죽거나, 로그에 `${JWT_SECRET}` 관련 property resolution 에러가 찍힌다.

**원인**: origin/main이 추가한 `application.yaml`의 `jwt.secret-key: ${JWT_SECRET}`은 기본값이 없다. `docker-compose.yml`의 `conservation-backend` 서비스에는 `JWT_SECRET`을 넘겨주는 줄이 아예 없었다(main 쪽 자기 compose 파일도 마찬가지였음 - 로컬 docker-compose로 이 조합을 실제로 띄워본 적이 없었던 것으로 보임).

**해결**: `docker-compose.yml`에 로컬 개발용 기본값을 추가했다.
```yaml
JWT_SECRET: ${JWT_SECRET:-local-dev-only-jwt-secret-change-me}
```
실제 배포(EKS)에서는 `k8s/app.yaml`이 `app-secrets`(k8s Secret)에서 주입받으므로 이 기본값과 무관하다 - 로컬 전용 안전장치다.

## 2. Hibernate `ddl-auto: validate`가 스키마 불일치로 기동 실패

**증상**: JWT_SECRET을 넣은 뒤에도 `SchemaManagementException: missing column [xxx] in table [yyy]`로 계속 죽는다.

**원인**: 로컬 postgres 볼륨이 오래돼서, origin/main이 추가한 `artifacts`(소유권/재질/시대 등 컬럼), `tasks`(timestamptz 컬럼) 스키마 변경을 못 받은 상태였다. `ddl-auto: validate`라 스키마가 안 맞으면 그냥 기동 실패로 죽는다 - 자동으로 고쳐주지 않는다.

**해결**:
1. `db/artifact_owner_migration.sql`, `db/tasks_timestamptz_migration.sql` 등 이미 레포에 있는 마이그레이션 파일을 psql로 직접 적용.
2. `xray_job.user_id`(uuid → bigint)는 **레포에 마이그레이션 파일 자체가 없던 진짜 gap**이었다 - `db/xray_job_user_id_migration.sql`을 새로 작성해서 추가했다.

**교훈**: 이 프로젝트는 Flyway/Liquibase 같은 마이그레이션 러너가 없다. 새 environment를 띄우거나 오래된 로컬 DB를 되살릴 때는 `db/*.sql`을 전부(순서 상관없이, 각 파일이 `IF NOT EXISTS` 기반이라 멱등적) 먼저 적용해야 한다. 새 컬럼/타입 변경을 추가하는 PR은 반드시 대응하는 `db/*.sql`을 같이 만들어야 한다 - Hibernate `update` 모드로 임시로 넘기면(컬럼만 대충 생기고 backfill/NOT NULL/인덱스가 빠짐) 나중에 다른 환경에서 또 이 문제가 반복된다.

## 3. `POST /api/vca` 403 Forbidden (이미지 업로드/분석 시작 실패)

**증상**: 로그인은 됐는데 이미지 업로드나 "분석 시작"을 누르면 `{"status":403,"error":"Forbidden","path":"/api/vca"}`.

**원인**: origin/main 병합으로 `WebSecurityConfig`가 `/api/vca/**`를 `.authenticated()`(로그인 JWT 필요)로 바꿨는데, FE `vcaApi.js`의 `requestJson`은 병합 전에 만들어진 코드라 `X-VCA-Access-Token`(vca-ai 외부 노출용 별개 공유 시크릿)만 붙이고 로그인 JWT(`Authorization: Bearer ...`)는 전혀 안 붙이고 있었다.

**해결**: `vcaApi.js`의 `requestJson`이 `authToken.js`의 `withAuthHeaders`로 로그인 토큰도 같이 붙이도록 수정. `/api/vca/**`를 타는 모든 호출이 이 함수 하나를 거쳐가서 한 곳만 고치면 됐다.

## 4. 새로고침하면 이미지가 안 보임 (MinIO 문제 아님)

**증상**: 업로드 직후에는 사진이 잘 보이는데, 페이지를 새로고침하면 깨진 이미지 아이콘만 뜬다.

**원인**: 업로드 직후엔 FE가 로컬 blob 미리보기(`URL.createObjectURL`)로 가려서 보이는 것뿐이었다. 새로고침하면 진짜 서버 URL(`/api/vca/{artifactId}/files/sha256/{sha256}` 게이트웨이 경로)을 쓰는데, `<img src>` 태그는 Authorization 헤더를 붙일 수 없다. `/api/vca/**`가 `.authenticated()`가 된 뒤로 이 경로가 JWT 없이 들어가서 403이 났다.

이미 `X-VCA-Access-Token`에 대해서는 정확히 이 문제(미디어 태그가 헤더를 못 붙임)를 풀려고 만든 `VcaAccessTokenInterceptor`의 쿼리파라미터 예외(`vca_access_token`)가 있었는데, 이 인터셉터는 Spring Security 필터체인보다 **뒤에** 실행돼서 JWT 단계를 못 구해준다.

**해결**: `JwtAuthenticationFilter.resolveToken()`에 같은 패턴(이미지/PDF 다운로드, GET 2개 경로에 한해)으로 `access_token` 쿼리 파라미터 예외를 추가. FE `vcaApi.js`의 `gatewayUrl()`이 로그인 JWT를 이 쿼리 파라미터로 같이 붙이도록 수정.

## 5. 네이티브(호스트) vca-ai로 전환 시 이미지 다운로드 실패

**증상**: vca-ai를 컨테이너 대신 맥북 네이티브 프로세스(MPS 쓰려고, `docker-compose.local-vca.override.yml`)로 돌리면, run 생성 시 `502 Bad Gateway: Failed to download ... <urlopen error [Errno 8] nodename nor servname provided, or not known>`.

**원인**: BE가 vca-ai에 넘기는 이미지 다운로드 URL을 `internalS3Presigner`로 만드는데, 이 프리사이너는 "vca-ai가 같은 docker 네트워크 안에 있다"고 가정하고 `http://minio:9000`(컨테이너 전용 호스트명)으로 URL을 만든다. 네이티브로 옮긴 vca-ai는 `minio`라는 이름을 못 찾는다.

**해결**: `S3Config`에 이미 있던 `aws.s3.internal-presign-endpoint` 오버라이드를 `docker-compose.local-vca.override.yml`에서 브라우저와 같은 host-reachable 주소로 채웠다.
```yaml
AWS_S3_INTERNAL_PRESIGN_ENDPOINT: ${AWS_S3_PRESIGN_ENDPOINT:-http://localhost:9000}
```

## 6. 오래 떠 있는 vca-ai 네이티브 프로세스가 최신 코드를 안 씀

**증상**: `ai-services/vca-ai/app/`을 고쳤는데 반영이 안 되는 것 같다.

**원인 및 구분**:
- **엔진 코드**(`vca_v2/modules/...`, 실제 분석 파이프라인)는 걱정 없다 - `run_vca()`가 run마다 `uv run python -m modules.orchestration.startup ...`을 **매번 새 서브프로세스로** 띄워서, 실행할 때마다 디스크에서 새로 읽는다.
- **FastAPI 래퍼**(`ai-services/vca-ai/app/*.py`, `uvicorn app.main:app`)는 다르다 - `--reload` 없이 띄운 장수 프로세스라, 이 폴더 안의 코드를 고쳐도 **재시작 전까지 반영 안 된다.**

**해결**: `ai-services/vca-ai/app/` 아래를 고쳤으면 반드시 그 uvicorn 프로세스를 재시작할 것. 재시작 전에 `pgrep -P <pid>`로 진행 중인 분석(엔진 서브프로세스)이 없는지 먼저 확인 - 있으면 그 run이 끊긴다.

## 7. 도자기 검사가 OPENAI_API_KEY 없이 안 됨

**증상**: `.env`에 `OPENAI_API_KEY`가 비어 있으면 도자기 검사의 문양 인식(VLM)만 실패하고 나머지(시대 판정 등 로컬 CNN 모델 기반)는 정상 동작한다.

**참고**: 문양 인식만 OpenAI를 쓴다. 로컬에서 OPENAI_API_KEY 없이 이 부분 UI만 빨리 확인하고 싶으면 `pottery_pattern_vlm_locator.py`에 `POTTERY_VLM_MOCK` 환경변수 스위치를 임시로 넣어 더미 데이터로 대체했던 적이 있다(현재는 원복된 상태 - 필요하면 git 히스토리에서 다시 참고). 실제 서비스/PR에는 절대 포함하지 말 것.
