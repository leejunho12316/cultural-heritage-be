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

## 엔드포인트

| 경로 | 설명 |
|---|---|
| `GET /health` | 상태 확인 |
| `POST /search` | report_rag 직접 검색 (디버깅용) |
| `POST /reports/generate` | `report_json` 생성 (LangGraph 실행, LLM 호출 발생) |
| `POST /reports/generate/docx` | 생성 + `.docx` 변환을 한 번에 (데모/직접 테스트용) |
| `POST /reports/docx` | 이미 만들어진 `report_json`을 `.docx`로 변환만 (LLM 재호출 없음) |

운영에서는 `/reports/generate`로 한 번 만든 `report_json`을
`ASSESSMENT_REPORT`에 저장해두고, 다운로드할 때마다 `/reports/docx`로
변환만 반복 요청하는 흐름을 권장합니다 (`/reports/generate/docx`를 매번
호출하면 LLM 비용이 중복 발생합니다).

## 사진 배치 방식

실제 보존처리 보고서 여러 건(국립박물관 보존과학 논문, 발굴조사 보존처리
보고서 등)을 직접 확인해서 정한 관행입니다: 사진을 문서 끝에 몰아 붙이지
않고, 관련 단계 서술이 끝나는 지점에 바로 이어 붙입니다. `photos`는
섹션 title이 아니라 `assemble.py`가 심어둔 안정적인 `key`(header/
pre_investigation/disassembly/cleaning/reinforcement/bonding/
restoration/conclusion) 기준으로 매칭합니다 — title 문자열은 LLM마다
표현이 조금씩 달라질 수 있어 매칭 기준으로 쓰기엔 불안정합니다.

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

## 알려진 한계 / 다음 단계

- **아직 어떤 파트도 report-ai를 실제로 호출하지 않습니다** — 보존가이드/
  X-ray/육안조사 각 파트의 결과를 모아서 요청 바디를 채워주는 조율
  계층이 아직 없습니다.
- X-ray는 최근 `XrayDefect`(리뷰 결정 `DAMAGE`/`NORMAL`) 테이블이
  생겼지만, report-ai가 기대하는 `xray_regions` 형태(`region_code`/
  `position`/`review_decision` 소문자)와는 필드명·대소문자가 다릅니다 —
  실제 연동 시 변환 어댑터가 필요합니다.
- 육안조사(pottery-inspection-ai)는 아직 결과를 DB에 저장하지 않습니다.
