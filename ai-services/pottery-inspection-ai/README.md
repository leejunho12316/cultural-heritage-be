# kt_bigp - 유물 육안조사 AI 모듈

사진 한 장으로 유물의 형태(완전/파편), 표면 광택, 시대, 문양 종류·위치,
문양별 보존 상태 이상 후보를 분석하는 AI 모듈입니다. BE의 X-RAY 모듈과는
완전히 별개이며, 이 모듈은 "육안 Text"(자연어 조사보고서 텍스트) 하나를
만들어 넘겨주는 역할을 합니다.

## 폴더 구조

```
kt_bigp/
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
│   ├── train_completeness_rf.py
│   ├── train_glaze_rf.py
│   └── glaze_rf.joblib          (v2 이전 버전, 참고용)
├── data_prep/                # 데이터 수집/정제/라벨링
│   ├── auto_label_from_name.py, auto_label_glaze_from_name.py
│   ├── download_by_era.py, emuseum_client.py
│   ├── rank_glaze_candidates.py
│   └── sort_by_auto_label.py, sort_by_glaze_label.py
├── eval/                      # 평가/검증
│   ├── evaluate_completeness.py
│   └── pottery_analysis_colab.ipynb
├── data/                      # 소규모 참고 데이터(CSV)
├── docs/
│   ├── API_SPEC.md              BE 인계용 API 스펙 (POST /inspect 상세)
│   └── CHANGELOG.md             개발 히스토리(버전별 변경 사항)
├── sample_images/              데모용 샘플 사진
├── ai_hub/                     (git 제외) 대용량 학습 데이터/모델 - 로컬 전용
├── archive/                     (git 제외) 지금 안 쓰는 옛 부식/균열 탐지 파이프라인
├── .env                         OPENAI_API_KEY 등 (git 제외)
├── Dockerfile                   BE 인계용 API 서버(app/pottery_api.py) 이미지
├── .dockerignore
└── requirements.txt
```

## 빠른 실행

```bash
cd app
pip install -r ../requirements.txt
export OPENAI_API_KEY=sk-...   # 또는 저장소 루트 .env에 저장

# 데모 웹앱 (발표 시연용)
python pottery_app.py

# BE 연동용 API 서버
pip install fastapi uvicorn "python-multipart"
uvicorn pottery_api:app --host 0.0.0.0 --port 8001
```

`completeness_rf.joblib`/`glaze_rf_v2.joblib`은 크기가 작아(1~3MB) git에
그대로 포함돼 있어 클론만 받으면 바로 씁니다. 시대 CNN 모델
(`ai_hub/pottery_multitask_model_v2`, 수십MB)은 용량 때문에 git에 안
올라가므로 팀 Drive 등에서 따로 받아 저장소 루트의 `ai_hub/` 안에
넣어야 합니다 - `pottery_analyzer.py`의 `ERA_MODEL_DIR`은 저장소
루트 기준 상대경로라 클론 위치와 무관하게 자동으로 찾습니다. 이
모델이 없어도 다른 기능은 정상 동작하고, 시대(CNN) 판정만 경고와
함께 비활성화됩니다.

## BE 연동

`docs/API_SPEC.md`에 `POST /inspect` 엔드포인트의 요청/응답 형식, 반환
JSON 구조, 전문가 재검토 우선순위 신호(`human_review_recommended`)가 언제
true가 되는지까지 정리해뒀습니다. 이 모듈은 DB 저장, 전문가 승인 UI,
조사보고서 페이지는 만들지 않습니다 - "육안 Text" 하나를 만들어 넘기는
것까지가 역할입니다.

파이썬 환경 세팅 없이 컨테이너로 바로 띄우고 싶다면 `docs/DOCKER.md`
참고 (저장소 루트의 `Dockerfile`로 빌드).

## 참고

- `docs/CHANGELOG.md`에 이 파이프라인이 어떤 문제를 겪고 어떻게 고쳐왔는지
  버전별로(v6~v11) 정리돼 있습니다. 판단 근거를 다시 확인하고 싶을 때
  참고하세요.
- `archive/`는 초반에 시도했던 부식/균열(YOLO-Seg 기반) 탐지 파이프라인을
  로컬 보관용으로만 옮겨둔 것입니다 - 지금은 쓰지 않고, git에도 올라가지
  않습니다.
- 정량 평가(라벨링된 정답 대비 정확도)는 아직 표본이 적습니다. 발표/제출
  전 실사진 몇 장으로 한 번 더 직접 돌려보는 걸 권장합니다.
