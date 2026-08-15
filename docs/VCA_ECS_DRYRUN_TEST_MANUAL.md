# VCA ECS Dry-run 테스트 매뉴얼

ECS에 올린 `conservation-backend`(+ `vca-ai`)가 RDS와 정상적으로 통신하고,
API 배선이 맞는지를 **실제 GPU/모델 없이(dry-run)** 확인하기 위한 절차입니다.
목적은 "결과 데이터가 정확한가"가 아니라 **"요청이 끝까지 흘러서 RDS에
스키마대로 행이 쓰이는가"** 확인입니다 — dry-run은 findings(균열/박락 등
실제 분석 결과)를 만들지 않으므로 그 부분은 이 테스트로 검증되지 않습니다
(별첨 참고).

---

## 0. 사전 준비

### 0-1. 필수 환경변수 체크리스트

ECS 태스크 정의(`conservation-backend` 컨테이너)에 아래 값들이 들어있는지
확인합니다. `.env.example`과 대응됩니다.

| 변수 | dry-run 테스트용 값 | 비고 |
|---|---|---|
| `SPRING_DATASOURCE_URL` | `jdbc:postgresql://<RDS 엔드포인트>:5432/conservation` | RDS 프라이빗 엔드포인트 |
| `SPRING_DATASOURCE_USERNAME` / `PASSWORD` | RDS 계정 | |
| `VCA_ACCESS_TOKEN` | 임의 값(팀 공유) | `/api/vca/**` 전체에 필요, 없으면 Spring이 기동 자체를 실패함 |
| `VCA_AI_BASE_URL` | `http://<vca-ai 서비스 디스커버리 주소>:8000` | ECS 서비스 간 통신 주소(Cloud Map 등) |
| `AWS_S3_BUCKET` / `AWS_REGION` | 실제 값 | 이미지 업로드 저장용 |
| `JWT_SECRET` | 임의 값 | |

`vca-ai` 컨테이너 쪽:

| 변수 | dry-run 테스트용 값 | 비고 |
|---|---|---|
| `VCA_RUN_MODE` | **`dry-run`** | 이 테스트의 핵심 스위치 |
| `VCA_DEVICE` | `cpu` (또는 아무 값) | dry-run은 실제 추론을 안 하므로 GPU 불필요 |
| `VCA_MODEL_CACHE_ROOT` | `/opt/vca-models/models` | 값이 비어있으면 안 됨(빈 문자열도 계약 위반으로 실패). 실제 모델 파일이 없어도 dry-run은 통과함 |
| `VCA_BOOTSTRAP_MODELS` | `false` | dry-run은 실제 모델 파일이 필요 없으므로 굳이 14GB 다운로드 안 해도 됨 |
| `VCA_RAG_CORPUS_ARCHIVE_URL` | 비워도 무방 | dry-run은 rag 단계까지 가지 않고 스킵되므로 corpus 없어도 통과 |

> ⚠️ **주의**: `VCA_ACCESS_TOKEN`이 빈 문자열이면 Spring이 기동 시점에 바로
> 죽습니다(`VcaAccessTokenInterceptor` 생성자에서 예외). ECS 태스크가 계속
> 재시작만 반복한다면 이것부터 의심하세요.

### 0-2. 접속 주소 확보

- ECS 서비스가 ALB 뒤에 있다면 ALB DNS 이름
- 없다면 팀 내부망(VPN 등)에서 ECS 태스크의 프라이빗 IP로 직접 접속

이 문서에서는 `$BASE_URL`로 표기합니다(예: `http://<alb-dns>` 또는
`http://<task-private-ip>:8080`).

---

## 1. 배포 상태 확인

### 1-1. ECS 콘솔/CLI로 태스크 상태 확인

```bash
aws ecs describe-services --cluster <클러스터명> --services <서비스명> \
  --query 'services[0].{status:status,running:runningCount,desired:desiredCount}'
```

`running == desired`가 아니면 태스크가 계속 죽고 있다는 뜻이니, 아래보다
먼저 CloudWatch Logs부터 봅니다(3장 참고).

### 1-2. 헬스체크

```bash
curl -sS -w '\nHTTP_STATUS=%{http_code}\n' "$BASE_URL/api/xray/health"
```

Spring이 뜬 상태라면 200이 나와야 합니다(이 엔드포인트 자체는 VCA 토큰이
필요 없음).

### 1-3. VCA 인증/DB 연결 동시 확인

```bash
curl -sS -w '\nHTTP_STATUS=%{http_code}\n' \
  -H "X-VCA-Access-Token: $VCA_ACCESS_TOKEN" \
  "$BASE_URL/api/vca"
```

- `200` + `[]`(빈 배열) → 정상. Spring이 RDS까지 붙어서 조회 쿼리를 실행한
  것이므로, **이 한 줄이 "RDS 연결 자체는 된다"는 가장 빠른 증거**입니다.
- `401` → 토큰 안 맞음 (0-1 확인)
- `500` → DB 연결 실패 가능성 높음. CloudWatch에서 `HikariPool`/
  `Connection refused`/`timeout` 로그 확인

---

## 2. Dry-run 엔드투엔드 테스트

### 2-1. 테스트용 아티팩트 ID 정하고 이미지 업로드

```bash
ARTIFACT_ID="ecs-dryrun-$(date +%s)"

curl -sS -w '\nHTTP_STATUS=%{http_code}\n' -X POST \
  -H "X-VCA-Access-Token: $VCA_ACCESS_TOKEN" \
  -F "file=@sample.jpg;type=image/jpeg;filename=sample.jpg" \
  "$BASE_URL/api/vca/$ARTIFACT_ID/images"
```

**기대 결과**: `201` + `imageId` 포함 JSON. (아무 jpg/png나 사용 가능 —
dry-run은 이미지 내용을 실제로 분석하지 않음)

여기서 응답에 나온 `imageId`를 기록해 둡니다.

### 2-2. 분석 run 생성

```bash
curl -sS -w '\nHTTP_STATUS=%{http_code}\n' -X POST \
  -H "X-VCA-Access-Token: $VCA_ACCESS_TOKEN" \
  "$BASE_URL/api/vca/$ARTIFACT_ID/runs"
```

**기대 결과**: `201` + `assessmentRunId`, `status: "RUNNING"` (또는
`QUEUED`). 이 `assessmentRunId`를 기록합니다.

### 2-3. 진행 상태 폴링

```bash
ASSESSMENT_RUN_ID="<위에서 받은 값>"

watch -n 5 curl -sS \
  -H "X-VCA-Access-Token: $VCA_ACCESS_TOKEN" \
  "$BASE_URL/api/vca/$ARTIFACT_ID"
```

(`watch` 없으면 5초 간격으로 반복 실행) `runs[].status`가
`RUNNING → COMPLETED`로 바뀔 때까지 기다립니다. dry-run은 실제 모델을
안 쓰므로 **수 초~길어야 1분 이내** 끝나야 정상입니다(RunPod 실측 기준
real 모드는 객체 수에 따라 수십 분씩 걸리지만, dry-run은 그것과 비교가
안 될 정도로 빠릅니다 — 오래 걸린다면 뭔가 잘못 설정된 것).

### 2-4. 리포트 조회 (스텁 내용 확인)

```bash
curl -sS -H "X-VCA-Access-Token: $VCA_ACCESS_TOKEN" \
  "$BASE_URL/api/vca/$ARTIFACT_ID/runs/$ASSESSMENT_RUN_ID/report" | python3 -m json.tool
```

**기대 결과**: `status: "COMPLETED"`, `findings: []`(빈 배열),
`summary`에 `"dry-run"`이 언급된 스텁 문구. **findings가 비어있는 게
정상**입니다 — 채워져 있으면 오히려 dry-run이 아니라 real로 돌았다는
뜻이니 `VCA_RUN_MODE` 설정을 다시 확인하세요.

---

## 3. RDS 스키마 확인 (핵심 목적)

`psql`로 RDS에 직접 붙어서 방금 만든 데이터가 들어갔는지 확인합니다
(VPN/bastion 등으로 네트워크 경로가 열려 있어야 함 — 별도 이슈).

```sql
-- 방금 올린 이미지가 들어갔는지
SELECT image_id, artifact_id, file_name, content_type, status, uploaded_at
FROM uploaded_image
WHERE artifact_id = '${ARTIFACT_ID}';

-- run이 들어갔는지
SELECT assessment_run_id, artifact_id, status, material, created_at, completed_at
FROM assessment_run
WHERE artifact_id = '${ARTIFACT_ID}';

-- 리포트가 들어갔는지 (findings는 비어있는 게 정상)
SELECT assessment_run_id, summary, findings
FROM assessment_report
WHERE assessment_run_id = '${ASSESSMENT_RUN_ID}';
```

테이블/컬럼명은 Spring `@Entity` 클래스(`src/main/java/.../vca/domain/`)
기준이며, `ddl-auto: update`라 실제 컬럼명이 다를 수 있습니다 — 위 쿼리가
"테이블/컬럼 없음" 에러를 내면 그 자체가 스키마 불일치를 발견한
것이니 바로 리포트해주세요.

체크리스트:
- [ ] `uploaded_image`에 방금 올린 이미지 행이 있다
- [ ] `assessment_run`에 방금 만든 run 행이 있고 `status = 'COMPLETED'`다
- [ ] `assessment_report`에 리포트 행이 있다 (findings는 비어있어도 정상)
- [ ] 문자 인코딩 문제 없음(한글 파일명 테스트 시 — 아래 4장 참고)

---

## 4. (선택) 한글 파일명 테스트

실제 유물 사진은 한글 파일명이 흔합니다. ECS 컨테이너의 JVM 로케일이
한글을 못 받으면 업로드가 500으로 죽는 문제가 로컬/RunPod 환경 둘 다에서
있었습니다(원인: `sun.jnu.encoding`이 UTF-8이 아닐 때
`VcaImageUploadValidator.safeFileName`이 `InvalidPathException`을 던짐).

```bash
curl -sS -w '\nHTTP_STATUS=%{http_code}\n' -X POST \
  -H "X-VCA-Access-Token: $VCA_ACCESS_TOKEN" \
  -F "file=@sample.jpg;type=image/jpeg;filename=16호 토광묘 2 청동대접 전면.jpg" \
  "$BASE_URL/api/vca/$ARTIFACT_ID/images"
```

`500`이 나오면 태스크 정의의 컨테이너 `command`/`entryPoint`에 아래를
추가해야 합니다(Dockerfile을 직접 못 고치는 상황이라면 태스크 정의의
환경변수/커맨드 오버라이드로):

```
LANG=C.UTF-8
LC_ALL=C.UTF-8
```

또는 JVM 실행 옵션에 `-Dfile.encoding=UTF-8 -Dsun.jnu.encoding=UTF-8`
추가.

---

## 5. 문제 생겼을 때 확인 순서

1. **ECS 태스크가 계속 재시작됨** → CloudWatch Logs에서 마지막 예외 확인.
   `VCA_ACCESS_TOKEN` 누락이 흔한 원인 (0-1 참고).
2. **`/api/vca` 호출이 500** → `HikariPool`/`Connection refused` 로그면
   RDS 네트워크(보안그룹, 서브넷 라우팅) 문제. `psql`로 직접 붙어서 먼저
   확인.
3. **업로드가 500** → 로그에 `InvalidPathException` 있으면 4장의 로케일
   문제. 그 외 스택트레이스면 그대로 공유.
4. **run이 `RUNNING`에서 안 넘어감** → `vca-ai` 컨테이너 로그 확인.
   `VCA_RUN_MODE`가 실제로 `dry-run`인지, `VCA_MODEL_CACHE_ROOT`가
   빈 문자열이 아닌지 재확인 (`ContractValidationError` 로그로 뜸).
5. **리포트에 findings가 채워져서 나옴** → dry-run이 아니라 real로 돈 것.
   env var 오타 의심.

---

## 별첨: dry-run이 실제로 실행하는 범위

| 단계 | dry-run 동작 |
|---|---|
| preprocessing | 실행됨 (모델 미호출, 배선만 검증) |
| rough_masking | 실행됨 (동일) |
| visual_cue_generation | 실행됨 (동일) |
| rag | 실행됨 (동일) |
| prompt_generating | 실행됨 (동일) |
| mask_refining | **스킵** (dry-run 계약 없음) |
| anomaly_grouping | **스킵** (mask_refining 결과 필요) |
| report_generating | **스킵** (anomaly_grouping 결과 필요) |

즉 findings(실제 분석 결과)는 절대 생성되지 않으며, `assessment_report`는
스텁 요약만 담긴 채 저장됩니다. **"API/DB 배선이 스키마대로 맞는지"**를
빠르게, GPU 없이 확인하는 용도로만 쓰세요 — 실제 분석 데이터 형태까지
검증하려면 `VCA_RUN_MODE=real`로 별도 테스트가 필요합니다.
