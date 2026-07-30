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

`postgres`(LangGraph 체크포인터 + 앱 DB), `conservation-guide-ai`(FastAPI, 8000), `conservation-backend`(Spring, 8080) 3개 컨테이너로 구성.

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
```

`OPENAI_API_KEY` 가 없으면 상태조사 문안 생성만 조용히 실패한다.
다른 기능은 정상 동작해서 원인을 찾기 어려우니 먼저 확인한다.

`POSTGRES_PASSWORD` 가 없으면 DB 가 뜨지 않아 Spring 도 기동하지
못한다.

### `shared/` (레포 루트)

Spring 과 xray-ai 가 결합 작업 파일을 주고받는 폴더다. Docker 가
만들어 주지만 미리 만들어 두면 권한 문제를 피할 수 있다.

```bash
mkdir -p shared
```

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
