# pottery-inspection-ai - 유물 육안조사 AI 모듈 (도자기)

사진 한 장으로 유물의 형태(완전/파편), 표면 광택, 시대, 문양 종류·위치,
문양별 보존 상태 이상 후보를 분석하는 AI 모듈입니다. BE의 X-RAY 모듈과는
완전히 별개이며, 이 모듈은 "육안 Text"(자연어 조사보고서 텍스트) 하나를
만들어 넘겨주는 역할을 합니다.

> 이 폴더는 `cultural-heritage-be` 레포의 `ai-services/pottery-inspection-ai/`
> 로 편입되어 있습니다. 아래 경로 설명은 모두 **이 폴더 자신을 기준**으로 한
> 상대경로입니다(BE 레포 전체 루트 기준이 아닙니다).

## 폴더 구조

```
ai-services/pottery-inspection-ai/
├── app/                    # 실행 서비스 - 실제로 돌아가는 코드는 전부 여기
│   ├── pottery_app.py          Gradio 데모 웹앱 (발표 시연용)
│   ├── pottery_api.py          FastAPI 서버 (BE 연동용, POST /inspect)
│   ├── pottery_analyzer.py     핵심 분석 파이프라인(형태/광택/시대/문양 통합)
│   ├── pottery_pattern_vlm_locator.py   VLM으로 문양 이름+위치+상태 분석
│   ├── precise_pattern_masking.py       유물 실루엣/문양 영역 마스킹
│   ├── predict_pottery_multitask.py     시대 CNN 추론
│   ├── completeness_classifier.py       완전/파편 RF 특징 추출
│   ├── glaze_detector.py                표면 광택 RF 특징 추출
│   ├── evaluate_glaze.py                이미지 로딩 유틸(한글 경로 안전 처리)
│   ├── completeness_rf.joblib, glaze_rf_v2.joblib   학습된 RF 모델
│   └── models/
│       └── grounded_sam_detector.py     Grounding DINO + SAM2 (유물 실루엣 추출)
├── training/                # 모델 학습 스크립트
├── data_prep/                # 데이터 수집/정제/라벨링
├── eval/                      # 평가/검증
├── data/                      # 소규모 참고 데이터(CSV)
├── docs/
│   ├── API_SPEC.md              BE 인계용 API 스펙 (POST /inspect 상세)
│   └── CHANGELOG.md             개발 히스토리(버전별 변경 사항)
├── sample_images/              데모용 샘플 사진
├── ai_hub/                      학습 데이터/모델 - 전부 git에 포함됨
│   ├── pottery_multitask_model_v2/   시대 CNN 모델 (실제로 쓰이는 버전)
│   ├── pottery_multitask_model/      구버전 - 코드에서 더 이상 안 씀(죽은 폴더, 정리 예정)
│   └── (manifest CSV, 라벨링/전처리 스크립트 등도 함께 포함)
├── archive/                     (git 제외) 지금 안 쓰는 옛 부식/균열 탐지 파이프라인
├── .env                         OPENAI_API_KEY 등 (git 제외)
├── Dockerfile                   컨테이너 이미지 빌드용
├── .dockerignore
└── requirements.txt
```

> `ai_hub/`는 원래 용량 때문에 대부분 git 제외 예정이었으나, 정리를
> 미루고 전부 그대로 커밋하기로 함 - **클론만 받으면 바로 동작**하는
> 대신 레포 용량이 필요 이상으로 큽니다. 나중에 여유 있을 때
> `pottery_multitask_model_v2`만 남기고 나머지(학습용 manifest, 데이터
> 준비 스크립트, 구버전 모델)는 `git rm --cached`로 정리하는 걸
> 권장합니다.

## 실행 방법 - 두 가지

### ① BE랑 같이, docker-compose로 (실제 운영 방식)

`cultural-heritage-be` 레포 루트에서:
```bash
docker compose up --build pottery-inspection-ai
```
`docker-compose.yml`에 등록된 서비스로 떠서, 내부적으로는
`http://pottery-inspection-ai:8001`로, BE를 거치면
**`POST http://localhost:8080/pottery-inspection`**으로 접근합니다.
FE/BE 개발자는 보통 이 경로로만 호출하면 됩니다. 요청/응답 형식은
`docs/API_SPEC.md` 참고.

`OPENAI_API_KEY`는 `conservation-guide-ai`(다른 조원의 AI 서비스)랑
**같은 환경변수를 공유**합니다 - `.env`에 한 번만 설정하면 됩니다.

### ② 이 모듈만 따로, 로컬 파이썬으로 (개발/디버깅용)

```bash
cd app
pip install -r ../requirements.txt
export OPENAI_API_KEY=sk-...   # 또는 이 폴더 루트 .env에 저장

# 데모 웹앱 (발표 시연용)
python pottery_app.py

# API 서버만 (BE 없이 단독 테스트)
pip install fastapi uvicorn "python-multipart"
uvicorn pottery_api:app --host 0.0.0.0 --port 8001
```
이 경우 `http://localhost:8001/inspect`로 직접 호출해서 테스트할 수
있습니다 (Swagger UI: `http://localhost:8001/docs`).

## BE 연동

`docs/API_SPEC.md`에 `POST /inspect` 엔드포인트의 요청/응답 형식,
반환 JSON 구조, 전문가 재검토 우선순위 신호(`human_review_recommended`)가
언제 true가 되는지까지 정리해뒀습니다. 이 모듈은 DB 저장, 전문가 승인 UI,
조사보고서 페이지는 만들지 않습니다 - "육안 Text" 하나를 만들어 넘기는
것까지가 역할입니다.

BE 쪽 연동 코드(`pottery_inspection_ai` 패키지: Client/Controller/DTO,
`RestClientConfig`, `WebConfig`의 CORS 설정)는 `feature/pottery-inspection-ai`
브랜치에 있습니다.

## 참고

- `docs/CHANGELOG.md`에 이 파이프라인이 어떤 문제를 겪고 어떻게
  고쳐왔는지 버전별로(v6~v11) 정리돼 있습니다. 판단 근거를 다시
  확인하고 싶을 때 참고하세요.
- `archive/`는 초반에 시도했던 부식/균열(YOLO-Seg 기반) 탐지 파이프라인을
  로컬 보관용으로만 옮겨둔 것입니다 - 지금은 쓰지 않고, git에도
  올라가지 않습니다.
- 정량 평가(라벨링된 정답 대비 정확도)는 아직 표본이 적습니다.
  발표/제출 전 실사진 몇 장으로 한 번 더 직접 돌려보는 걸 권장합니다.