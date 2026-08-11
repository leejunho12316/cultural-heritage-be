# report-ai — 보존처리 보고서 자동생성 AI 모듈

보존가이드(해체·세척·강화처리·접합·복원)·X-ray·육안조사 세 파트의 결과를
받아서, 고정된 "도자기 처리보고서" 양식(8개 섹션)에 맞춰 문장을 생성하고
편집 가능한 `.docx` 파일로 변환하는 AI 모듈입니다. LangGraph로 만든
9노드 그래프이며, 노드 하나당 섹션 하나를 담당합니다.

> 이 폴더는 `cultural-heritage-be` 레포의 `ai-services/report-ai/`로
> 편입되어 있습니다. 아래 경로 설명은 모두 **이 폴더 자신을 기준**으로 한
> 상대경로입니다.

## report-ai가 하지 않는 것

- **GUIDE_TASK / XRAY_JOB / INSPECTION_RESULT_POTTERY를 직접 조회하지
  않습니다.** 호출자(Spring)가 각 파트 API 결과를 모아서 요청 바디에
  채워 보내야 합니다.
- **DB에 아무것도 저장하지 않습니다.** `report_json`을 만들어 돌려주기만
  하고, `ASSESSMENT_REPORT` 저장은 호출자 몫입니다.
- 사진 파일을 직접 조회하지 않습니다 — `.docx`에 사진을 넣고 싶으면
  호출자가 base64로 인코딩해서 `photos`에 넣어 보내야 합니다.

## 폴더 구조

```
ai-services/report-ai/
├── app/
│   ├── main.py              FastAPI 서버 (엔드포인트는 아래 참고)
│   ├── graph.py              9노드 LangGraph 조립
│   ├── state.py               그래프 State 정의
│   ├── schemas.py             ReportSection (LLM structured output)
│   ├── llm.py                  텍스트/vision 모델
│   ├── docx_export.py         report_json -> .docx 변환 (섹션별 사진 배치 포함)
│   ├── rag.py / build_index.py   report_rag 검색 (문체 참고용 TF-IDF)
│   ├── manifest.json           참고문서 메타데이터
│   ├── nodes/                  섹션별 노드 (header/pre_investigation/
│   │                            guide_stage×5/conclusion/assemble)
│   ├── documents/               참고 PDF 원문 (git 제외 — README 참고)
│   ├── index/                    TF-IDF 인덱스 (git 제외)
│   └── templates/report_templates.json   사람이 큐레이션한 구조 예시
├── Dockerfile
└── .dockerignore
```

`app/documents/*.pdf`, `app/index/*`는 `.gitignore`에서 제외됩니다 —
저작권 있는 원문 텍스트를 git에 올리지 않기 위함입니다
(`app/documents/README.md`에 정책 설명). `manifest.json`과
`templates/report_templates.json`(사람이 직접 검토해서 만든 구조)만
커밋됩니다.

## DB 구조 (ERD)

report-ai 자신은 DB를 전혀 조회하지 않지만, 입력으로 받는 데이터가 각
파트에서 실제로 어떤 테이블에 저장돼 있는지 알아야 어댑터/요청을 만들 수
있습니다. `cultural-heritage-be` 쪽 관련 테이블만 추린 ERD입니다.

```mermaid
erDiagram
    ASSESSMENT_RUN ||--o| ASSESSMENT_REPORT : "1:1 (assessment_run_id 공유 PK)"
    ASSESSMENT_RUN ||--o{ INSPECTION_RESULT_POTTERY : "assessment_run_id FK"
    ASSESSMENT_RUN ||--o{ REPORT_PDF_JOB : "assessment_run_id FK"
    XRAY_JOB ||--o{ XRAY_DEFECT : "xray_job_id FK"
    ARTIFACTS ||--o{ REPORT_DOCUMENT : "artifact_id FK (진짜 FK)"

    REPORT_DOCUMENT {
        uuid id PK
        uuid artifact_id FK
        jsonb report_json "report-ai 산출물 저장처 (report-ai 전용)"
        string docx_object_key "영구 S3 key, presign은 조회 시점마다 새로 발급"
    }
    ARTIFACTS {
        uuid artifact_id PK
        string name
        string category
        string material
        string era
        string weight
        string bonding_area
        string treatment_purpose
        string representative_image_key
    }
    ASSESSMENT_RUN {
        uuid id PK
        uuid artifact_id "FK 아님(ARTIFACTS.artifact_id와 값은 같지만 제약 없음)"
        int run_number "artifact_id+run_number UNIQUE"
        string status "queued/running/completed/failed"
        string material
        jsonb stages_json
        jsonb uploaded_image_ids_json
    }
    ASSESSMENT_REPORT {
        uuid assessment_run_id PK_FK
        jsonb report_json "report-ai 산출물 저장처"
        string status
        string overall_condition
        string risk_level
        timestamp generated_at
    }
    INSPECTION_RESULT_POTTERY {
        uuid id PK
        uuid assessment_run_id FK
        text inspection_text
        bool human_review_recommended
        jsonb detail
    }
    UPLOADED_IMAGE {
        uuid id PK
        uuid artifact_id "FK 아님"
        string object_key
        string status "PENDING/UPLOADED"
    }
    REPORT_PDF_JOB {
        uuid id PK
        uuid assessment_run_id FK
        string status "QUEUED/RUNNING/COMPLETED/FAILED"
        string pdf_object_key
    }
    XRAY_JOB {
        uuid id PK
        uuid artifact_id "FK 아님, UNIQUE (유물당 1건)"
        string status
        text report_text
    }
    XRAY_DEFECT {
        bigint id PK
        uuid xray_job_id FK
        string origin_type
        jsonb geometry "bbox 좌표만, 캔버스 크기 없음"
        string review_decision "DAMAGE/NORMAL"
    }
    TASKS {
        string task_id PK "artifact_id와 같은 값이라 추정 - 미확인"
        jsonb relic_info
        jsonb results "완료 결과, stage별 status 포함"
        string total_state
    }
```

**주의 — `artifact_id`는 대부분 실제 FK가 아닙니다.** 2026-08-11에
`artifacts` 테이블(유물 기본정보)이 생겼지만, 기존 테이블들은 아직 이걸
FK로 참조하지 않고 각자 독립적으로 `artifact_id`(UUID) 컬럼만 들고
있습니다(`ASSESSMENT_RUN`/`XRAY_JOB`/`UPLOADED_IMAGE`). `report_document`는
`artifacts`가 생긴 뒤에 만든 신규 테이블이라 처음부터 진짜 FK로
연결했습니다. `TASKS`(보존가이드)는 그마저도 없고 `task_id`가
`artifact_id`와 같은 값이라는 컨벤션에 기대는 것으로 보이는데, 이 레포
코드만으로는 확정할 수 없습니다(프론트엔드 확인 필요).

| 테이블 | 소유 파트 | report-ai 연동 |
|---|---|---|
| `artifacts` | 유물 등록(공용) | ✅ `ArtifactSourceAdapter` |
| `xray_job` / `xray_defect` | X-ray | ✅ `XraySourceAdapter` |
| `assessment_run` / `inspection_result_pottery` | 육안조사 | ⚠️ 어댑터(`PotterySourceAdapter`)는 있지만, 이 테이블에 실제로 값을 채워 넣는 코드가 아직 없음(FE `feature/vca_v2_fe`가 준비 중인 `/api/vca/*` BE 엔드포인트가 아직 없음) — BE 작업 진행 중이라 report-ai 쪽은 대기 |
| `report_document` | **report-ai 전용 신규 테이블** | ✅ report-ai가 만드는 `report_json` + 변환된 `.docx`의 실제 저장처. `assessment_report`(vca 파이프라인 산출물)와는 **별개 테이블** — `assessment_run` 존재 여부와 무관하게 `artifact_id`만으로 저장/조회한다 (2026-08-11 팀 결정) |
| `assessment_report` | 육안조사(vca, 공용) | report-ai와 무관 — vca 파이프라인 자체 산출물 저장처. 혼동 방지를 위해 report-ai는 이 테이블을 쓰지 않는다 |
| `uploaded_image` | 공용(사진) | 미연동 — `.docx` 사진은 아직 호출자가 base64로 직접 인코딩해서 넘김 |
| `report_pdf_job` | 보고서(공용) | 이름이 "PDF"라 Word로 확정된 것과 불일치 — 팀 확인 필요. report-ai는 `report_document`를 쓰므로 이 테이블은 안 씀 |
| `tasks` | 보존가이드 | ❌ 미연동 — `task_id`/`artifact_id` 매핑 확인 전까지 어댑터 보류 |

## 엔드포인트 (FastAPI, report-ai 자체)

| 경로 | 설명 |
|---|---|
| `GET /health` | 상태 확인 |
| `POST /search` | report_rag 직접 검색 (디버깅용) |
| `POST /reports/generate` | `report_json` 생성 (LangGraph 실행, LLM 호출 발생) |
| `POST /reports/generate/docx` | 생성 + `.docx` 변환을 한 번에 (데모/직접 테스트용) |
| `POST /reports/docx` | 이미 만들어진 `report_json`을 `.docx`로 변환만 (LLM 재호출 없음) |

운영에서는 `/reports/generate`로 한 번 만든 `report_json`을 Spring이
`report_document`에 저장해두고(아래 Spring API 명세 6번 `/save`),
다운로드할 때마다 `/reports/docx`로 변환만 반복 요청하는 흐름을
권장합니다 (`/reports/generate/docx`를 매번 호출하면 LLM 비용이 중복
발생합니다). report-ai 자신은 이 저장을 하지 않으므로, 저장은 항상
Spring 쪽 엔드포인트를 거쳐야 합니다.

## Spring API 명세 (`/api/reports/*`, 팀이 실제로 호출하는 창구)

FE/BE 팀원은 report-ai를 직접 호출하지 않고 이 Spring 엔드포인트를
씁니다. `cultural-heritage-be`의 `report_ai` 패키지(Client/Controller/
DTO/Adapter)가 위 FastAPI 엔드포인트를 감싼 것입니다.

### 1. 보고서 생성 (`report_json`만)

| 항목 | 내용 |
|---|---|
| Method / URL | `POST /api/reports/generate` |
| 설명 | 보존가이드·X-ray·육안조사 결과를 받아 LangGraph로 `report_json` 생성 (LLM 호출 발생) |
| Request Body | `{ "artifact_id": string, "relic_info": object, "guide_result": object, "xray_report_text": string, "xray_regions": [ { "region_code": string, "position": string, "review_decision": "damage"\|"normal", "user_note": string } ], "pottery_inspection": { "inspection_text": string, "human_review_recommended": boolean, "detail": object }, "photos": { "<section_key>": [ { "caption": string, "image_base64": string } ] } }` |
| Response | `report_json` 객체 자체: `{ "report_type": "ceramic_treatment_report", "artifact_id": string, "sections": [ { "key", "title", "fields" } \| { "key", "title", "body" } ] }` |
| 비고 | `section_key` = `header/pre_investigation_xray/pre_investigation_visual/disassembly/cleaning/reinforcement/bonding/restoration/conclusion` (`pre_investigation`만 X-ray/육안조사 두 key로 나뉨 — "사진 배치 방식" 참고). 매번 호출하면 LLM 비용 중복 — 한 번 생성 후 저장해서 재사용 권장 |

### 2. 보고서 생성 + `.docx` 변환 (한 번에)

| 항목 | 내용 |
|---|---|
| Method / URL | `POST /api/reports/generate/docx` |
| 설명 | 1번과 동일한 파이프라인 실행 후 바로 `.docx`로 변환 (데모/직접 테스트용) |
| Request Body | 1번과 동일 |
| Response | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` 바이너리, `Content-Disposition: attachment; filename="report_{artifactId}.docx"` |
| 비고 | 운영에서 매번 호출 금지 (LLM 비용 중복) — 1번 + 3번 조합 권장 |

### 3. 저장된 `report_json` → `.docx` 변환만

| 항목 | 내용 |
|---|---|
| Method / URL | `POST /api/reports/docx` |
| 설명 | 이미 만들어진 `report_json`을 `.docx`로 변환 (LLM 재호출 없음) |
| Request Body | `{ "artifact_id": string, "report_json": object, "photos": { "<section_key>": [ { "caption": string, "image_base64": string } ] } }` |
| Response | 2번과 동일한 `.docx` 바이너리 |
| 비고 | 운영 다운로드 흐름은 이 엔드포인트 반복 호출이 정석 |

### 4. X-ray 결과 조회 (report-ai 입력 형태 변환)

| 항목 | 내용 |
|---|---|
| Method / URL | `GET /api/reports/{artifactId}/xray-source` |
| 설명 | `xray_job`/`xray_defect`(DAMAGE만)를 report-ai 입력 형태로 변환 |
| Response | `{ "xray_report_text": string, "xray_regions": [ { "region_code", "position", "review_decision": "damage", "user_note": "" } ] }` |
| 비고 | job 없음/`artifactId`가 UUID 아님 → 빈 값 반환(에러 아님). 응답을 그대로 1/2번의 `xray_report_text`/`xray_regions`에 채우면 됨. 담당 어댑터: `XraySourceAdapter` |

### 5. 유물 기본정보 조회 (report-ai 입력 형태 변환)

| 항목 | 내용 |
|---|---|
| Method / URL | `GET /api/reports/{artifactId}/relic-info-source` |
| 설명 | `artifacts` 테이블을 report-ai 입력 형태로 변환. `Artifact.era` → `relic_info.period`로 이름만 바뀜(그 외 필드는 이름 동일) |
| Response | `{ "id": string, "artifact_code": string, "name": string, "material": string, "period": string, "weight": string, "bondingArea": string, "treatmentPurpose": string }` |
| 비고 | 유물이 없거나 `artifactId`가 UUID 아님 → 빈 객체 반환(에러 아님). 응답을 그대로 1/2번의 `relic_info`에 채우면 됨. 담당 어댑터: `ArtifactSourceAdapter` |

### 6. 보고서 저장 (`report_document`에 실제 저장)

| 항목 | 내용 |
|---|---|
| Method / URL | `POST /api/reports/{artifactId}/save` |
| 설명 | 미리보기까지 끝난 `report_json`을 `.docx`로 변환해 S3에 영구 저장하고, 그 결과를 `report_document`에 기록 (LLM 재호출 없음 — 내부적으로 3번과 같은 변환만 함) |
| Request Body | `{ "reportJson": object, "photos": { "<section_key>": [ { "caption": string, "image_base64": string } ] } }` |
| Response `201` | `{ "id": string, "artifactId": string, "reportJson": object, "docxDownloadUrl": string, "createdAt": string, "updatedAt": string }` |
| 비고 | 유물이 없으면 `404`. `docxDownloadUrl`은 그 시점에 새로 발급한 presigned URL(1시간) — 응답을 캐싱해서 나중에 재사용하면 만료될 수 있음, 매번 7번으로 다시 조회할 것 |

### 7. 저장된 보고서 조회 (게시판 등에서 재조회용)

| 항목 | 내용 |
|---|---|
| Method / URL | `GET /api/reports/{artifactId}` |
| 설명 | 이 유물의 가장 최근 저장 보고서(`report_document`)를 조회 |
| Response | 6번과 동일한 형태 |
| 비고 | 저장된 보고서가 없으면 `404` |

## 사진 배치 방식

실제 보존처리 보고서 여러 건(국립박물관 보존과학 논문, 발굴조사 보존처리
보고서, 규장각 보고서 등)을 직접 확인해서 정한 관행입니다: 사진을 문서
끝에 몰아 붙이지 않고, 관련 조사/단계 서술이 끝나는 지점에 바로 이어
붙입니다. `photos`는 섹션 title이 아니라 안정적인 `key` 기준으로
매칭합니다 — title 문자열은 LLM마다 표현이 조금씩 달라질 수 있어
매칭 기준으로 쓰기엔 불안정합니다.

키 목록: `header`/`pre_investigation_xray`/`pre_investigation_visual`/
`disassembly`/`cleaning`/`reinforcement`/`bonding`/`restoration`/
`conclusion`.

**`pre_investigation`(처리 전 상태조사)만 예외적으로 세부 key 2개를
씁니다.** 이 섹션은 X-ray 조사와 육안조사라는 서로 다른 데이터 출처를
한 섹션 안에서 다뤄서, 예전엔 둘을 하나의 LLM 호출로 묶어 사진도
`pre_investigation` 키 하나에 다 붙였습니다 — 그 결과 사진이 두 조사
서술이 **전부 끝난 뒤**에야 붙어서, 참고 보고서들의 관행("문양 서술
직후 문양 사진, 그다음 다음 항목")과 어긋났습니다(2026-08-11 발견 및
수정). 지금은 `pre_investigation_node`가 X-ray/육안조사를 각각 독립된
LLM 호출로 만들어 `report_json`에 `parts` 리스트로 담고, `docx_export`가
part마다 본문을 쓴 직후 그 part의 key로 사진을 붙입니다 — 그래서
`photos`도 `pre_investigation`이 아니라 `pre_investigation_xray`/
`pre_investigation_visual` 두 key로 나눠 보내야 합니다.

**하위호환**: `cultural-heritage-fe` PR #21(2026-08-11 병합)처럼 아직
구버전 방식대로 `pre_investigation` 키 하나에 사진을 몰아 보내는
호출자가 있어서, `docx_export`가 그 키로 들어온 사진을 캡션 문구
("X-ray"/"엑스레이" → X-ray part, "육안"/"문양" → 육안조사 part)로
자동 분류해서 알맞은 part에 붙여줍니다. 캡션으로도 분류 안 되는
사진은 잃어버리지 않게 섹션 맨 끝에 붙입니다. 새로 연동하는
호출자는 처음부터 `pre_investigation_xray`/`pre_investigation_visual`
키를 직접 쓰는 게 맞고, 이 하위호환은 기존 호출자용 안전장치입니다.

## 실행 방법

### ① BE랑 같이, docker-compose로

`cultural-heritage-be` 레포 루트에서:
```bash
docker compose up --build report-ai
```
내부적으로 `http://report-ai:8000`, 로컬에서는 `http://localhost:8002`.
BE를 거치면 `POST http://localhost:8080/api/reports/*`로 접근합니다
(Spring 연동 코드: `report_ai` 패키지 — Client/Controller/DTO,
`RestClientConfig`).

### ② 이 모듈만 따로, 로컬 파이썬으로 (개발/디버깅용)

```bash
cd app
pip install -r requirements.txt
export OPENAI_API_KEY=sk-...
uvicorn app.main:app --host 0.0.0.0 --port 8000
```
`http://localhost:8000/docs`에서 Swagger UI로 직접 테스트할 수 있습니다.

인덱스를 처음부터 새로 만들려면(참고 PDF를 바꿨을 때):
```bash
python build_index.py
```
