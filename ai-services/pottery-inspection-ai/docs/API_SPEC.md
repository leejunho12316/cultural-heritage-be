# 육안조사 AI 모듈 - BE 인계 스펙

이 문서는 `app/pottery_api.py`(FastAPI)가 제공하는 HTTP 엔드포인트 하나를
BE가 그대로 호출해 쓸 수 있도록 정리한 문서입니다. X-RAY 모듈과는 완전히
별개이며, 이 모듈은 X-RAY 데이터를 전혀 받지도 쓰지도 않습니다. 사진
한 장을 받아 "육안 Text" 하나를 만들어 돌려주는 게 이 모듈의 유일한 역할이고,
DB 저장·전문가 승인 화면·조사보고서 페이지(분석서 Page)는 BE/FE가 만듭니다.

## 실행

```
cd app
pip install fastapi uvicorn "python-multipart"
uvicorn pottery_api:app --host 0.0.0.0 --port 8001
```

`app/pottery_analyzer.py` 상단의 `ERA_MODEL_DIR`을 실제 시대 CNN 모델
경로로 맞춰야 합니다(현재는 `ai_hub/pottery_multitask_model_v2` 절대
경로를 가리킵니다). `DEFAULT_COMPLETENESS_MODEL`/`DEFAULT_GLAZE_MODEL`은
`app/completeness_rf.joblib`, `app/glaze_rf_v2.joblib`을 자동으로
찾습니다(스크립트와 같은 폴더). 저장소 루트의 `.env`에 `OPENAI_API_KEY`도
필요합니다(문양 분석 VLM 호출용) - `python-dotenv`가 상위 폴더까지 자동
탐색하므로 `app/` 안에서 실행해도 문제없습니다.

> **참고**: 코드 검증에 쓴 샌드박스 환경에는 fastapi가 설치돼 있지 않아
> 실제 서버 기동 테스트는 못 했습니다. `analyze_pottery()`를 감싸는
> 로직 자체(엔드포인트 함수 본문)는 스텁으로 별도 검증했지만, uvicorn으로
> 실제로 띄워서 curl/Postman으로 호출하는 테스트는 실행 환경에서
> 한 번 확인해 주세요.

## 엔드포인트

### `GET /health`

헬스체크. 응답:
```json
{"status": "ok", "module_version": "pottery-inspection-v11"}
```

### `POST /inspect`

**요청**: `multipart/form-data`
| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `image` | file | O | 유물 사진(jpg/png 등) |
| `n_calls` | int (쿼리 파라미터) | X (기본 3) | 문양 분석 VLM 반복 호출 횟수. 1이면 합의 검증 없이 단일 결과만 씀(빠르지만 신뢰도 낮음) |
| `use_vlm_pattern` | bool (쿼리 파라미터) | X (기본 true) | false면 문양 분석(VLM) 자체를 건너뛰고 형태/광택/시대만 반환(비용 절감) |

**응답 (200)**:
```json
{
  "module_version": "pottery-inspection-v11",
  "inspection_text": "[유물 외형]\n사진상 외형은...\n\n[주요 문양]\n...\n\n[상태 이상 후보]\n...\n\n[종합 의견]\n...",
  "summary": "형태: 사진상 외형 기준 완전 추정 (모델 점수 99%) / 표면 광택 수준: 낮음 ... ",
  "human_review_recommended": true,
  "detail": { "...analyze_pottery()가 반환하는 전체 JSON..." }
}
```

| 필드 | 설명 |
|---|---|
| `inspection_text` | **DB의 "조사 결과 Text"에 저장할 값.** 유물 외형/주요 문양/상태 이상 후보/종합 의견 4문단으로 구성된 자연어 텍스트. 화이트보드 플로우의 "육안 Text"에 해당. |
| `summary` | 한 줄 기술 요약(슬래시로 필드 나열). 로그나 대시보드 목록에서 짧게 보여줄 때 참고용. 조사보고서 페이지에는 `inspection_text`를 쓰는 걸 권장. |
| `human_review_recommended` | AI가 최종 승인/반려를 내리는 게 아니라, "전문가 검토 대기열에서 이 건을 먼저 봐야 할 가능성이 높다"는 우선순위 힌트(bool). 아래 "human_review_recommended가 true가 되는 조건" 참고. |
| `detail` | 형태/광택/시대/문양별 상세 정보를 담은 전체 JSON. 당장 화면에 안 써도 저장해두면 나중에 문양별 근거를 다시 보여줄 때 유용. 구조는 아래 참고. |

**오류 응답**:
- `400`: 빈 파일 등 요청 자체가 잘못됨
- `413`: 업로드 파일이 20MB 초과(`MAX_UPLOAD_SIZE_BYTES`, pottery_api.py 상단에서 조정 가능)
- `422`: 사진에서 유물 영역을 못 찾는 등, 입력 사진 문제로 분석이 불가능함 (`detail`에 사유 텍스트)
- `500`: 서버/모델 쪽 오류 (`detail`에 오류 메시지)

넷 다 FastAPI 기본 오류 형식 `{"detail": "..."}`으로 옵니다.

**CORS**: FE가 브라우저에서 이 API를 직접 호출할 가능성을 대비해 전체
origin을 허용해뒀습니다(`allow_origins=["*"]`). 실제로는 BE가 중계
호출만 한다면 없어도 무방하지만, 있다고 문제가 생기지도 않습니다. 배포
단계에서는 프론트 도메인으로 좁히는 걸 권장합니다.

## `human_review_recommended`가 true가 되는 조건

다음 중 하나라도 해당하면 true (`pottery_analyzer.needs_human_review()` 참고):
- 분석 자체가 실패함(`status != "성공"`) 또는 문양 분석(VLM)이 오류로 실패함
- 클로즈업 상태조사에서 "경미한 이상 의심/이상 후보 관찰/뚜렷한 이상 의심" 중 하나가 관찰된 문양이 있음
- 문양별 상태조사 결과에 `human_review_required: true`가 있음
- 근거 부족으로 판정보류된 문양이 있음(명칭 미확정)
- VLM 반복 호출 중 일부가 실패했거나, 애초에 `n_calls=1`로 합의 검증 없이 돌림

이 신호를 BE의 승인 대기열 정렬/필터에 그대로 써도 되고, 무시하고
모든 건을 동일하게 다뤄도 됩니다 - 어느 쪽이든 최종 승인은 전문가가 합니다.

## `detail`(전체 JSON) 구조 요약

```
detail
├── status: "성공" | 그 외(실패 사유)
├── completeness: { prediction, score, interpretation, features, limitation }
├── glaze: { prediction, score, interpretation, explanation, features, limitation }
├── era: { prediction, score, interpretation, source, limitation }
└── pattern_era_color
    ├── overall_description: string
    ├── uncertainty: string (VLM이 남긴 전반적 불확실성 메모)
    ├── min_agreement_used / calls_requested / calls_succeeded
    ├── condition_review_enabled: bool
    └── patterns: [
          {
            key,                      # 문양 하나를 고유하게 식별하는 값 -
                                       # 전문가가 이 문양의 이름/상태를
                                       # 수정할 때 이 key로 매칭하면 됨
            pattern_name, display_name, alternative_candidate,
            decision,                 # "확정" | "추정" | "판정보류"
            agreement_count/total,    # 몇 회 중 몇 회 위치가 일치했는지(정확성 아님, 재현성)
            badge,                    # 이미지 오버레이 상의 배지 텍스트(상단띠1 등)
            confidence,               # VLM 자체 판단 확신도(높음/중간/낮음)
            visible_evidence / missing_evidence,
            condition: {              # 확정 계열 문양에만 존재(판정보류/저합의는 없음)
              condition_status,       # "특이사항 없음" | "경미한 이상 의심" |
                                       # "이상 후보 관찰" | "뚜렷한 이상 의심" | "판정불가"
              issues: [{issue_type, confidence, alternative_explanation}],
              condition_description,
              human_review_required,  # 이 문양 하나에 대한 재검토 필요 여부
              confidence,
            },
          },
          ...
        ]
```

`pattern_name`은 사전에 정의한 참고 목록 기준 1차 후보명이고, `display_name`은
이름 합의가 낮으면 "A/B 계열(명칭 불확실)"처럼 표시용으로 조정된 값입니다.
전문가 승인 UI에서 "문양명 수정" 필드는 `display_name`(또는 `pattern_name`)을
초기값으로 채우고, 승인 시 최종값을 이 `key`에 매핑해 저장하면 됩니다.

## 알아두면 좋은 것

- **비용/시간**: 사진 한 장당 VLM 호출이 최소 `n_calls`회(기본 3) + 확정된
  문양 수만큼(클로즈업 상태조사, 문양당 1회) 발생합니다. 타임아웃은
  넉넉히(60~120초) 잡는 걸 권장합니다.
- **버전 표시**: `module_version`은 이 코드 자체의 버전 문자열입니다.
  DB에 같이 저장해두면 나중에 프롬프트/로직이 바뀐 뒤 예전 결과와 구분할
  수 있습니다.
- **X-RAY 연동 없음**: 이 모듈은 X-RAY 데이터를 입력으로 받지 않습니다.
  "X-RAY 결합 Text"와 이 모듈의 `inspection_text`("육안 Text")를 하나로
  합치는 로직이 필요하다면 그건 BE 쪽에서 처리해야 합니다.
