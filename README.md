# conservation_backend

문화재 보존처리 관리 시스템 — Spring 백엔드 + LangGraph 기반 AI 서비스(`ai-services/conservation-guide-ai`)로 구성.

## 기술 스택 버전

### Spring 백엔드 (`/src`)

| 항목                                     | 버전                                                   |
| ---------------------------------------- | ------------------------------------------------------ |
| Java                                     | 17 (`Dockerfile`: `eclipse-temurin:17-jdk` / `17-jre`) |
| Spring Boot                              | 4.1.0                                                  |
| io.spring.dependency-management 플러그인 | 1.1.7                                                  |
| Build tool                               | Gradle (`gradlew`)                                     |
| DB (로컬/테스트)                         | H2                                                     |

주요 의존성: `spring-boot-starter-data-jpa`, `spring-boot-starter-validation`, `spring-boot-starter-webmvc`, Lombok(compile-only), `spring-boot-devtools`(dev), JUnit Platform(test). 버전은 Spring Boot 4.1.0 BOM이 관리.

### conservation-guide-ai (`/ai-services/conservation-guide-ai`)

| 항목                             | 버전                      |
| -------------------------------- | ------------------------- |
| Python (배포 기준, `Dockerfile`) | 3.12 (`python:3.12-slim`) |

`app/requirements.txt` 고정 버전:
| 패키지 | 버전 |
|---|---|
| langgraph | 1.2.9 |
| langgraph-checkpoint-postgres | 3.1.0 |
| psycopg[binary,pool] | 3.3.4 |
| langchain-openai | 1.3.5 |
| fastapi | 0.139.2 |
| uvicorn | 0.51.0 |
| pydantic | 2.13.4 |
| python-dotenv | 1.2.2 |

> 로컬 개발 venv가 3.12보다 최신 Python으로 구성되어 있다면, 배포 환경(Docker)과의 문법 호환성 문제가 없는지 유의할 것.

### 인프라 (`docker-compose.yml`)

| 항목       | 버전          |
| ---------- | ------------- |
| PostgreSQL | `postgres:16` |

`postgres`(LangGraph 체크포인터 + 앱 DB), `conservation-guide-ai`(FastAPI, 8000), `xray-ai`(FastAPI, 8001), `pottery-inspection-ai`(FastAPI, 8003), `vca-ai`(FastAPI, 8004), `conservation-backend`(Spring, 8080) 컨테이너로 구성.

---

---

## X-RAY 분석 파트 README

---

조각 결합, 이상영역 탐지, 상태조사 문안 생성을 담당한다.

| 서비스                 | 역할                            | 포트 |
| ---------------------- | ------------------------------- | ---- |
| `conservation-backend` | Spring. 모든 요청의 관문        | 8080 |
| `xray-ai`              | 결합 엔진, YOLO 탐지, 문안 생성 | 8001 |

프론트엔드는 Spring 만 호출한다. AI 서비스를 직접 부르지 않는다.

> `docker compose up` 은 `postgres` 와 `conservation-guide-ai` 도
> 함께 띄운다. X-RAY 와는 무관하지만 Spring 이 DB 에 의존하므로
> 모두 떠 있어야 한다.

---

---

## VCA 분석 파트 README

---

유물 사진의 육안 상태조사(균열·오염·변색 등 시각적 이상 탐지)를
`vca_v2`(SAM2/Qwen2.5-VL/RAG) 파이프라인으로 자동화한다. 도자기·백자
재질 유물은 별도 AI로 조사서 초안까지 이어서 만든다.

| 서비스                  | 역할                                              | 포트 |
| ----------------------- | ------------------------------------------------- | ---- |
| `conservation-backend`  | Spring. 모든 요청의 관문                          | 8080 |
| `pottery-inspection-ai` | 도자기 재질 유물 후속 검사(완전성/유약/시대/문양) | 8003 |
| `vca-ai`                | `vca_v2` 파이프라인 어댑터(FastAPI)               | 8004 |

실제 요청 경로는 Spring이 Docker 내부 네트워크로 `vca-ai:8000`/
`pottery-inspection-ai:8000`을 호출하는 것이다. 위 8003/8004 host port는
디버깅용으로만 열려 있다.

### 분석 흐름

1. 이미지 업로드 후 `POST /api/vca/{artifactId}/runs`로 run을 생성하면
   vca_v2 8단계 파이프라인(`preprocessing → rough_masking →
   visual_cue_generation → rag → prompt_generating → mask_refining →
   anomaly_grouping → report_generating`)이 순서대로 실행된다.
2. run이 처음 `COMPLETED`로 전환되는 순간, 재질 문자열에 "도자"/
   "pottery"/"ceramic"이 포함되면 도자기 후속 검사가 서버에서 자동
   트리거된다 - FE 버튼 클릭이 필요 없다. 실패해도 VCA 리포트
   자체는 그대로 조회 가능하고, `POST .../pottery-inspection`으로
   수동 재시도할 수 있다.
3. 실패한 run은 `POST /api/vca/{artifactId}/runs`에
   `{"resume": true}`를 보내면 이미 끝난 스테이지는 다시 돌지 않고
   실패 지점부터 이어서 실행한다(직전 실패 run과 정확히 같은 이미지
   집합일 때만 - 아티팩트 상세 응답의 `resumableRunId`가 null이
   아니면 이어가기가 가능하다는 뜻).
4. `POST .../report/pdf`로 리포트 PDF를 만든다. object storage가
   설정된 환경에서는 Apache PDFBox로 진짜 PDF(표지+대표사진 →
   특이점 전체 오버레이+요약 → 특이점별 상세 페이지 → 도자기 검사
   결과가 있으면 그 페이지까지)를 동기 렌더링해 저장한다. object
   storage가 없는 데모 모드는 즉시-COMPLETED로 전환되는 스텁이다.

### 도자기 검사 결과 저장

도자기 검사 결과는 `assessment_run` 테이블이 아니라 별도 테이블
`inspection_result_pottery`(run 1개당 결과 최대 1건,
`assessment_run_id` UNIQUE)에 저장한다. 진행/실패/재시도 같은
워크플로우 상태는 `assessment_run.pottery_inspection_status_json`에
그대로 남아있다 - run 자신의 `status`/`failure_reason`과 같은
성격의 process metadata라 결과 테이블과 분리해서 유지한다.

### RAG 문서 corpus

VCA는 이상 유형 판정 근거로 문헌 PDF corpus를 검색한다. `vca-ai`
컨테이너는 `VCA_RAG_CORPUS_ARCHIVE_URL`에 지정된 아카이브를 최초
기동 시 자동으로 받아 채우고, 이미 채워져 있으면 다시 받지 않는다
(갱신은 캐시를 지우고 재기동하는 명시적 동작으로만 한다). 개별
문서를 추가/삭제하려면 `GET`/`POST`/`DELETE /api/vca/corpus/pdfs`를
쓴다.

---

## 실행

```bash
docker compose up --build -d
docker compose ps
```

첫 빌드는 Gradle 과 torch 설치 때문에 5~10분 걸린다.

기동 확인:

```bash
docker compose logs --tail 20 conservation-backend
docker compose logs --tail 20 xray-ai
```

`Started ConservationBackendApplication` 과 `모델 로드 완료` 가
보이면 준비된 것이다. Spring 은 40초, xray-ai 는 모델을 올리는 데
1~2분 걸린다.

```bash
curl http://localhost:8080/api/xray/health
```

`aiServiceHealthy` 가 `true` 여야 한다.

> 코드를 고친 뒤에는 반드시 다시 빌드한다. `docker compose up -d`
> 만으로는 예전 이미지가 그대로 뜬다.

---

## 저장소에 없는 것

`.gitignore` 로 제외되어 있어 직접 준비해야 한다.

### `.env` (레포 루트)

```env
OPENAI_API_KEY=...
POSTGRES_PASSWORD=...

AWS_REGION=ap-northeast-2
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_S3_BUCKET=AWS-버킷-이름...

VCA_ACCESS_TOKEN=...
VCA_RUN_MODE=real
VCA_DEVICE=auto
VCA_MODEL_CACHE_ROOT=/opt/vca-models/models
VCA_BOOTSTRAP_MODELS=true
VCA_S3_OBJECT_PREFIX=vca/images
VCA_RAG_CORPUS_ARCHIVE_URL=...
```

`OPENAI_API_KEY` 가 없으면 상태조사 문안 생성만 조용히 실패한다.
다른 기능은 정상 동작해서 원인을 찾기 어려우니 먼저 확인한다.

`POSTGRES_PASSWORD` 가 없으면 DB 가 뜨지 않아 Spring 도 기동하지
못한다.

`VCA_ACCESS_TOKEN` 이 없으면 `docker compose config` 단계에서 실패한다.
Spring의 `/api/vca/**`는 `X-VCA-Access-Token` 헤더가 일치해야 접근할 수
있다. `VCA_RUN_MODE=dry-run`은 GPU/모델 없이 FE → Spring → `vca-ai` →
`vca_v2` 배선만 검증하는 smoke 모드이고, `real`은 실제 모델을 돌린다.
AWS_S3_* 는 X-RAY와 VCA가 함께 쓰는 object storage 설정이다.
`VCA_BOOTSTRAP_MODELS=true`면 `vca-ai` 컨테이너가 시작할 때 Hugging
Face 모델을 `VCA_MODEL_CACHE_ROOT` 아래로 받는다(named volume에
캐시되므로 첫 실행만 오래 걸린다). `VCA_RAG_CORPUS_ARCHIVE_URL`은
아래 "RAG 문서 corpus" 참고.

#### AWS 콘솔에서 발급받는 방법

1. IAM 관련
   AWS Console - IAM - IAM 사용자에 들어가 사용자 생성을 눌러주세요.
   사용자 이름을 입력하고 다음을 누르세요.
   IAM 사용자에는 `AmazonS3FullAccess`를 부여하지 않습니다.

   대신 대상 버킷에 한정된 `xray-test-s3-app-policy` {추후 이름 변경 예정/지금은 테스트 용}를 연결합니다.
   - 버킷 권한: `s3:GetBucketLocation`, `s3:ListBucket`
   - 객체 권한: `s3:GetObject`, `s3:PutObject`
   - 객체 범위: `<bucket-arn>/xray/*`
     사용자 생성을 완료하세요.

   생성된 IAM 사용자를 클릭하세요.
   액세스 키 만들기 - 로컬 코드를 선택해 액세스 코드를 생성하세요.
   3단계에 나오는 액세스 키와 비밀 액세스 키를 .env에 입력하면 됩니다.
   -> AWS_ACCESS_KEY_ID : 액세스 키
   -> AWS_SECRET_ACCESS_KEY : 비밀 액세스 키

2. S3 관련
   AWS Console - S3에 들어가 버킷을 하나 만드세요
   ACL 비활성화, 모든 퍼블릭 액세스 차단, 버킷 버전 관리 비활성화 등으로 전부**기본 설정**을 그냥 사용하세요.

   -> AWS_S3_BUCKET : S3 버킷을 만들고 버킷 이름을 적어주시면 됩니다.
   -> AWS_REGION : AWS 인프라가 구축되어 있는 리전을 입력해주시면 됩니다. (ap-northeast-2, us-east-1,,,)

### `shared/` (레포 루트)

Spring 과 AI 서비스가 작업 파일을 주고받는 폴더다. Docker 가 만들어
주지만 미리 만들어 두면 권한 문제를 피할 수 있다.

```bash
mkdir -p shared/vca/input-store shared/vca/engine-output shared/vca/document-corpus
```

X-RAY는 `shared/` 바로 아래를 쓰고, VCA는 `shared/vca/` 아래를 쓴다 -
`conservation-backend`(run 입력 이미지 준비)와 `vca-ai`(엔진 입출력)가
같은 호스트 디렉터리를 각자의 컨테이너 경로로 마운트해서 공유한다.
`conservation-backend`에 이 마운트가 빠지면 이미지가 컨테이너 자체
파일시스템에만 써지고 사라져서 `vca-ai`가 매번 "입력 폴더 없음"으로
분석을 거절한다.

### `ai-services/xray-ai/models/*.pt`

학습된 YOLO 가중치. 담당자에게 받아 `models/` 아래에 둔다.

### `ai-services/xray-ai/samples/`

통합 테스트용 이미지. 아래 구조로 준비한다.

```
samples/
├── composite.jpg      결함 탐지 회귀 기준 (탐지 6건)
├── front.jpg          컬러 기준 이미지
└── spoon001/          X-RAY 조각 이미지
```

---

## 통합 테스트

```bash
cd ai-services/xray-ai
python test_integration.py \
  --fragments samples/spoon001 --color samples/front.jpg
```

결함 탐지 회귀 기준은 **결합본 6건**이다. 이 숫자가 달라지면
`app/config.py` 의 `DEFAULT_CONF`, `NMS_IOU`, `IMGSZ_ASSEMBLED` 를
확인한다. 학습 환경과 맞춰 고정한 값이다.

---

## API

```
GET  /api/xray/health                     AI 서비스 상태

POST /api/xray/stitch/jobs                결합 작업 접수
GET  /api/xray/stitch/jobs/{id}           상태 폴링
GET  /api/xray/stitch/jobs/{id}/result    결합본 이미지
GET  /api/xray/stitch/jobs/{id}/layout    조각별 배치 정보

POST /api/xray/detect/assembled           결합본 이상영역 탐지
POST /api/xray/detect/fragments           원본 조각 이상영역 탐지
POST /api/xray/report                     상태조사 문안 생성
```

### 결합이 비동기인 이유

조각 수에 따라 수 분에서 수십 분이 걸린다. 동기 호출로 두면
브라우저나 프록시가 먼저 연결을 끊어 결과를 받을 수 없다.
작업을 접수하고 `jobId` 를 먼저 돌려준 뒤 상태를 폴링한다.

Spring 이 업로드를 `shared/jobs/{jobId}/` 에 저장하고, xray-ai 에는
파일이 아니라 경로만 전달한다. 두 컨테이너가 같은 볼륨을 본다.

`layout` 은 결합 엔진이 만든 `layout.json` 을 그대로 내려준다.
조각별 변환행렬이 담겨 있어 화면에서 수동 보정에 쓴다.

### 결합 엔진

`ai-services/xray-ai/` 아래 `scripts/`, `xray_assembler/`, `configs/`,
`mappings/` 가 엔진이다. 별도 CLI 도구를 서비스에서 subprocess 로
호출한다.

**엔진 코드는 이 저장소에서 수정하지 않는다.** 변경이 필요하면
원본 담당자에게 요청한다.

---

## VCA API

모든 경로는 `/api/vca` 아래이고 `X-VCA-Access-Token` 헤더가 필요하다
(이미지/PDF 다운로드 두 GET 경로만 `?vca_access_token=` 쿼리로도 허용).

```
GET    /api/vca                                            아티팩트 목록
GET    /api/vca/{artifactId}                                아티팩트 상세(runs, resumableRunId 포함)
GET    /api/vca/system-info                                  동작 환경 정보(엔진/모델 버전 등)

POST   /api/vca/{artifactId}/images/presign                  업로드용 presigned URL 발급
POST   /api/vca/{artifactId}/images                          이미지 직접 업로드
POST   /api/vca/{artifactId}/images/{imageId}/complete       presigned 업로드 완료 통지
DELETE /api/vca/{artifactId}/images/{imageId}                이미지 삭제(참조 중인 run 있으면 차단)
GET    /api/vca/{artifactId}/files/sha256/{sha256}           이미지 파일 게이트웨이(303 리다이렉트)

POST   /api/vca/{artifactId}/runs                            분석 run 생성(body: material, resume)
POST   /api/vca/{artifactId}/runs/{assessmentRunId}/cancel   run 취소
GET    /api/vca/{artifactId}/runs/{assessmentRunId}/report               리포트 조회
GET    /api/vca/{artifactId}/runs/{assessmentRunId}/intermediate-results 중간 산출물 미리보기

POST   /api/vca/{artifactId}/runs/{assessmentRunId}/pottery-inspection   도자기 검사 수동 재시도
POST   /api/vca/{artifactId}/runs/{assessmentRunId}/report/pdf           리포트 PDF 생성
GET    /api/vca/{artifactId}/report-pdf-jobs/{jobId}                     PDF job 상태 조회
GET    /api/vca/{artifactId}/report-pdf-jobs/{jobId}/download            PDF 다운로드(303 리다이렉트)

GET    /api/vca/corpus/pdfs                                  RAG 문서 corpus 목록
POST   /api/vca/corpus/pdfs                                  RAG 문서 corpus 업로드
DELETE /api/vca/corpus/pdfs/{fileName}                        RAG 문서 corpus 삭제
```

`vca_v2` 엔진 소스는 `ai-services/vca-ai/engine`에 벤더링돼 있어
Docker 이미지 안의 `/vca_v2`에서 실행된다. **엔진 코드는 이 저장소에서
직접 수정하지 않는다** - 원본 `vca_v2` 저장소에서 고친 뒤 다시
벤더링한다.

---

## 기술 스택 버전

### Spring 백엔드 (`/src`)

| 항목                                     | 버전                                                   |
| ---------------------------------------- | ------------------------------------------------------ |
| Java                                     | 17 (`Dockerfile`: `eclipse-temurin:17-jdk` / `17-jre`) |
| Spring Boot                              | 4.1.0                                                  |
| io.spring.dependency-management 플러그인 | 1.1.7                                                  |
| Build tool                               | Gradle (`gradlew`)                                     |
| DB (로컬/테스트)                         | H2                                                     |

주요 의존성: `spring-boot-starter-data-jpa`,
`spring-boot-starter-validation`, `spring-boot-starter-webmvc`,
Lombok(compile-only), `spring-boot-devtools`(dev),
JUnit Platform(test). 버전은 Spring Boot 4.1.0 BOM 이 관리.

### xray-ai (`/ai-services/xray-ai`)

| 항목                             | 버전                      |
| -------------------------------- | ------------------------- |
| Python (배포 기준, `Dockerfile`) | 3.12 (`python:3.12-slim`) |
| torch / torchvision              | 2.5.1 / 0.20.1 (CPU)      |

`app/requirements.txt` 고정 버전:

| 패키지                 | 버전      |
| ---------------------- | --------- |
| fastapi                | 0.115.6   |
| uvicorn[standard]      | 0.34.0    |
| python-multipart       | 0.0.20    |
| ultralytics            | 8.4.106   |
| opencv-python-headless | 4.10.0.84 |
| numpy                  | 1.26.4    |
| pillow                 | 11.1.0    |
| openai                 | >=2.48.0  |

> `ultralytics` 는 학습 환경과 맞춰야 한다. 다른 버전에서 저장한
> `.pt` 를 구버전으로 로드하면 오류가 나거나 결과가 달라진다.
>
> `opencv` 는 반드시 headless 를 유지한다. 일반 `opencv-python` 은
> GUI 백엔드를 요구해 컨테이너에서 죽는다.

> 로컬 개발 venv 가 3.12 보다 최신 Python 으로 구성되어 있다면,
> 배포 환경(Docker)과의 문법 호환성 문제가 없는지 유의할 것.

---

## X-ray S3·Lambda·RDS 통합

X-ray 결합은 더 이상 Docker `shared` volume이나 EFS를 사용하지 않습니다. 입력은 presigned URL로 S3에 업로드하고, FastAPI는 URL로 입력을 받아 임시 작업공간에서 처리한 뒤 결과를 S3에 저장합니다. 작업 상태는 RDS `xray_job`, S3 객체 기록은 Lambda가 `s3_file`에 저장합니다.

구조와 FE 계약은 [`docs/XRAY_S3_RDS_INTEGRATION.md`](docs/XRAY_S3_RDS_INTEGRATION.md), 참고 DDL은 [`db/xray_s3_rds.sql`](db/xray_s3_rds.sql)을 확인하세요.
