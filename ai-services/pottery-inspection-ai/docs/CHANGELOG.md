# 개발 히스토리 (도자기 육안조사 AI 모듈)

## v12 - 저장소 정리 (git 업로드 전 대청소)

- 배경: 프로젝트가 여러 실험(부식/균열 탐지 → 도자기 육안조사로 전환)을
  거치면서 저장소 루트가 지저분해졌습니다(0바이트 정크 파일, 옛 부식
  탐지 파이프라인 잔재, 대용량 파일이 git에 걸려있는 문제 등). git에
  올리기 전 정리를 요청받아 다음을 진행했습니다.
- 0바이트 정크 파일 13개 삭제(`0`, `0.68`, `bbox가`, `list[list[tuple[int`,
  `np.ndarray`, `실패한`, `탐색` 등 - 터미널/붙여넣기 실수로 생긴 빈 파일들).
- 옛 부식/균열(corrosion/crack) 탐지 파이프라인을 `archive/`(git 제외,
  로컬 전용)로 이동: `corrosion_yolo_seg/`(883개 라벨 파일), `datasets/`,
  `analyzer.py`/`app.py`(도자기 이전의 구버전 분석기/앱), `train.py`,
  `train_corrosion_seg.py`, `convert_*.py`, `crack.yaml`, `schemas.py`
  (DamageItem 등 부식 탐지 전용 스키마), `yolo11n-seg.pt`/`yolo26n.pt`,
  `models/damage_detector.py`/`damage_candidate_extractor.py`/
  `vlm_inspector.py`, `models/weights/*.pt`, `batch_eval.py`,
  `sweep_yolo_confidence.py`, `visualize_seg_labels.py`. 현재 활성
  파이프라인(`app/` 이하)이 이 파일들 중 어느 것도 import하지 않는다는
  걸 grep으로 먼저 확인한 뒤 옮겼습니다.
- git에 이미 추적되고 있던 대용량/legacy 경로를 `git rm -r --cached`로
  언트랙(로컬 파일은 유지): `corrosion_yolo_seg/`(883개), `runs/`(61개),
  `outputs/`(45개), `__pycache__/`, `models/weights/*.pt`,
  `yolo11n-seg.pt`/`yolo26n.pt`, `analyzer.py`/`app.py`/`train.py`
  외 legacy 스크립트들. `.gitignore`에 이미 있던 규칙인데 나중에
  추가되는 바람에 실제로는 계속 추적되고 있던 상태였습니다.
- `.gitignore` 확장: `*.joblib`, `*.zip` 추가, `ai_hub/`(대용량 학습
  데이터+모델, 83MB짜리 zip 포함)·`emuseum_pottery/`·
  `emuseum_pottery_all/`·`sorted_glaze_v2/`·`archive/` 명시적으로 추가.
- 활성 파이프라인을 `app/`(실행 서비스)·`training/`(RF 학습)·
  `data_prep/`(데이터 수집/정제)·`eval/`(평가)·`data/`(소규모 참고
  CSV)·`docs/`(문서) 폴더로 재배치. `pottery_analyzer.py`가 상대경로로
  찾는 `completeness_rf.joblib`/`glaze_rf_v2.joblib`과, import하는
  `models/grounded_sam_detector.py`도 함께 `app/`로 옮겨 경로가 깨지지
  않게 했습니다.
- **검증**: 재배치가 가장 리스크가 큰 작업이라(임포트가 깨지면 전체
  파이프라인이 죽음), 스텁 환경(dotenv/openai/pydantic/torch/
  torchvision/transformers/sam2/scipy/joblib/gradio 최소 스텁)을
  `app/` 새 위치에 직접 구성해 `pottery_analyzer.py`/`pottery_app.py`를
  실제로 import해봤습니다. `models.grounded_sam_detector` 상대 임포트,
  `completeness_classifier`/`glaze_detector`/`evaluate_glaze`/
  `predict_pottery_multitask`/`pottery_pattern_vlm_locator`/
  `precise_pattern_masking` 임포트, `DEFAULT_COMPLETENESS_MODEL`/
  `DEFAULT_GLAZE_MODEL` 경로(`app/completeness_rf.joblib`,
  `app/glaze_rf_v2.joblib`)가 실제 파일 존재까지 확인됐습니다.
  `pottery_api.py`는 이 환경에 fastapi가 없어 import는 못 했지만
  `py_compile`로 문법은 확인했습니다.
- BE 인계용 `pottery_api.py`(FastAPI 래퍼)와 `docs/API_SPEC.md`도 이번에
  `app/`, `docs/`로 함께 편입했습니다(직전 세션에서 별도 작업 폴더에
  만들었던 걸 정식 저장소로 옮김).
- 하지 않은 것: 실제 `git add`/`git commit`/`git push`는 하지 않았습니다
  (팀 공유 히스토리에 영향을 주는 작업이라 검토 후 직접 실행하는 걸
  권장). 또한 이미 원격(origin)에 커밋된 과거 히스토리 안의 대용량
  파일(예: `corrosion_yolo_seg`가 "first commit"에 통째로 들어간 것)을
  지우는 히스토리 재작성(git filter-repo/BFG)은 하지 않았습니다 -
  팀원들이 이미 clone한 저장소가 있다면 force-push가 필요한 위험한
  작업이라 별도로 논의 후 진행하는 걸 권장합니다.

---

## v11 - BE 인계용 API 추가 + 시각화/마스크 정밀화

### BE 인계 대응 (신규 파일 2개)
- 배경: 팀 전체 플로우(화이트보드 공유)를 보니 X-RAY와 육안조사는 완전히
  별개 모듈이고(X-RAY 데이터 안 씀), 이 모듈이 만들어야 하는 건 "육안 Text"
  하나뿐이며, DB 저장/전문가 승인 UI/조사보고서 페이지(분석서 Page)는
  BE/FE가 만든다는 게 확인됐습니다. AI 파트를 BE+FE에 빨리 넘겨야 해서
  (2일), 여기서는 보고서 파일을 직접 만들지 않고 대신 "BE가 바로 갖다
  쓸 수 있는 형태"로 다듬었습니다.
- pottery_analyzer.py에 두 함수 추가:
  - build_inspection_text(result): build_report_sentence()가 만드는
    슬래시로 이어붙인 기술 요약과 달리, 유물 외형/주요 문양/상태 이상
    후보/종합 의견 4문단짜리 자연어 텍스트를 만듭니다. 이게 화이트보드의
    "육안 Text"이고, DB의 "조사 결과 Text" 필드에 그대로 저장하면 됩니다.
    내용은 항상 기존 JSON 필드에서만 가져오고 새로 지어내지 않습니다.
  - needs_human_review(result): 전문가 검토 대기열 우선순위용 bool 신호.
    상태 이상 후보/human_review_required/판정보류 문양/VLM 호출 일부 실패
    /n_calls=1 중 하나라도 있으면 true. AI가 최종 승인·반려를 내리는 게
    아니라 "먼저 봐야 할 가능성이 높다"는 힌트일 뿐입니다.
- pottery_api.py (신규): analyze_pottery() 파이프라인을 FastAPI로 감싼
  HTTP 서버. POST /inspect가 이미지를 받아 inspection_text/summary/
  human_review_recommended/detail(전체 JSON)을 반환합니다. GET /health도
  있습니다. CORS 미들웨어와 업로드 파일 크기 제한(20MB)도 포함.
- API_SPEC.md (신규): BE가 pottery_api.py를 바로 갖다 쓸 수 있도록 엔드포인트
  요청/응답 형식, detail JSON 구조, human_review_recommended 판단 기준,
  비용/타임아웃 권장값을 정리한 인계 문서.
- 발표 시연용 Gradio 반영: pottery_app.py에도 build_inspection_text/
  needs_human_review를 연결. 화면에 "검토 우선순위" 배지와 "조사보고서
  (육안 Text)" 텍스트박스를 추가해 슬래시 요약보다 보고서처럼 보이게 함.
- 이번 라운드에서 하지 않은 것(의도적 범위 제외): DB 저장, 전문가 수정·승인
  UI, 조사보고서 문서(PDF/DOCX) 생성, 과거 조사 이력 비교, 손상 탐지 전용
  모델(YOLO-Seg/이상탐지), 촬영 품질 사전검사. 전부 BE/FE 영역이거나
  이번 2일 범위 밖으로 판단해 뺐습니다.

### 시각화 오해 소지 완화 + 상단띠 마스크 누수 수정
- 배경: 실제 용문 청자 사진으로 상세 검토 모드를 확인해보니, 초록색
  윤곽선이 상단/하단 띠뿐 아니라 용 머리 주변까지 넓게 퍼져 보여서
  "SAM 2가 문양 전체를 정밀 분할한 것"처럼 오해할 소지가 있었습니다.
  원인은 두 가지: (1) detail 모드 윤곽선이 진하고 두꺼워 시각적으로
  과도하게 촘촘해 보임, (2) build_border_band_mask()가 세로 범위는
  잉크 밀도로 좁히면서 가로는 유물 폭 전체를 그대로 훑어, 용 머리가
  상단 띠와 비슷한 높이일 때 그 잉크가 섞여 들어감(ROI 설정 문제).
- 표시 개선: 채우기 알파를 올리고 윤곽선은 낮춰 채우기가 주 신호가
  되게 함. 범례에 pattern_scope 기반 안내 문구 추가("근사 주요 영역",
  "문양 후보 영역·근사 추정"). SAM 2가 실루엣만 추출하고 개별 문양은
  색상 대비로 추정한다는 문장을 앱 하단에 명시.
- 마스크 누수 수정: build_border_band_mask()가 VLM의 bbox_percent(가로
  범위)를 가로 탐색 범위의 출발점으로 쓰도록 변경. bbox 폭의 10% 여유
  + 유물 폭의 65% 미만이면 대칭 확장 + 제한 결과가 원래보다 50% 미만
  으로 줄면 안전하게 전체 폭으로 폴백. method 문자열에 "_bbox_roi"/
  "_bbox_roi_fallback"이 붙어 JSON에서 경로 구분 가능.
- 검증: 인접 문양 잉크가 섞이는 합성 이미지 재현 테스트, bbox 없을 때
  회귀 없음 확인, bbox가 틀렸을 때 폴백 확인, detail 모드 렌더링/범례
  실제 실행 확인.

---

## v10 - 클로즈업 상태평가 언어를 확정형 → 의심/후보형으로 완화

- 배경: v9에서 실제 고려청자 사진으로 테스트했을 때, condition_status가
  "부분훼손"처럼 단정적으로 나오면서 동시에 confidence는 "중간"으로만
  나오는 내적 모순이 있었습니다. 사진 한 장만으로는 실제 손상(마모/박락/
  변색)과 촬영 조건(블러/압축/초점/조명/유약색)을 구분하기 어렵다는 지적,
  특히 박락(안료 결손)은 단순히 어두운 얼룩만으로는 확신할 수 없고
  단차/경계가 보여야 한다는 지적을 반영했습니다. 문양 명칭 쪽은 이미
  확정/추정/판정보류로 조심스럽게 설계해놓고, 상태 평가만 그 원칙을
  벗어났던 셈입니다.
- PatternConditionAssessment의 condition_status를 "양호/경미한마모/
  부분훼손/심한훼손/판정불가"에서 "특이사항 없음/경미한 이상 의심/이상
  후보 관찰/뚜렷한 이상 의심/판정불가"로 변경(모두 "의심/후보" 수준
  표현). 평평한 visible_issues/issue_evidence 리스트를 항목별 구조체
  issues: [{issue_type, confidence, alternative_explanation}]로 바꿔,
  각 이상 후보마다 "손상이 아닐 수도 있는 대안 설명"을 함께 반환.
  human_review_required(bool) 필드 추가. CONDITION_PROMPT_TEMPLATE도
  박락은 단차/경계가 보일 때만 판단하도록, 화질/압축/조명 등 대안 설명을
  항상 고려하도록 재작성.
- pottery_analyzer.py: _run_condition_review() 결과 구조 갱신,
  build_report_sentence()의 훼손 요약 문구를 "이상 후보 관찰(사람 재검토
  필요)"로 변경. predict_era()에 "limitation" 필드 추가(softmax 출력값이지
  보정된 확률이 아님, 교차검증 정확도 88%임을 명시). 시대 문구도 "시대
  후보: X (모델 점수 Y%, 형태·양식 기반 참고 결과)"로 완화.
- pottery_app.py: build_condition_gallery_items()가 새 issues 구조를
  항목별로(확신도 + 대안 설명 포함) 캡션에 나열, human_review_required
  =True면 "[사람 재검토 권장]" 표시.

---

## v9 - 박스/마스킹 방식에서 클로즈업 방식으로 전환

- 문양 영역을 정밀한 박스/마스크로 그리려던 접근을 계속 고집하는 대신,
  이미 확정된 문양마다 원본 사진에서 넉넉하게 크롭한 클로즈업 이미지를
  만들어 VLM에게 다시 한번 보여주고, 그 문양의 보존 상태를 평가하도록
  요청하는 기능을 추가. 정밀 마스킹이 반복적으로 새로운 버그(구연부
  침범, 창 클리핑, 오버사이즈 bbox 병합 등)를 냈던 것과 달리, 크롭
  박스는 "정확한 경계"가 아니라 "이 근처를 넉넉히 포함"하기만 하면
  되므로 훨씬 안정적. VLM은 확대된 이미지의 디테일을 읽는 데 강해서
  마모/박락 같은 상태 평가에 더 적합.
- PatternConditionAssessment 스키마 + CONDITION_PROMPT_TEMPLATE + 문양당
  단일 호출 함수 assess_pattern_condition() + 병렬 평가
  assess_pattern_conditions_in_parallel() 추가. 비용/시간을 고려해
  앙상블 없이 문양당 1회만 호출.
- 확정된 문양마다 스코프별 여백을 적용해 크롭하고 병렬로 상태 평가를
  요청하는 _run_condition_review() 추가. 결과는 patterns_out JSON과
  pattern_bundle["condition_gallery"]에 노출. ENABLE_CONDITION_REVIEW
  =False로 언제든 끌 수 있음.
- 참고: 정밀 마스킹 코드(build_border_band_mask/get_pattern_mask 등)는
  그대로 남겨뒀음. border_band에서만 여전히 쓰이고(claimed_mask 계산용)
  나머지는 안전모드로 우회.

---

## v8 - 논문 2편 기반 문양 판별 기준 보강

- 주미경(2014), 「한국 도자기 문양의 특성과 상징성 연구 -기하형 문양을
  중심으로-」를 반영해 기하학적 문양 5개(뇌문, 만자문, 파형문, 팔괘문,
  태극문)를 KNOWN_PATTERN_HINTS/PATTERN_CRITERIA에 추가. pattern_family
  Literal 타입에도 "기하문" 대분류 추가.
- 조원교(2019), 「고려·조선시대 도자기의 蓮華文 연구」의 논지(모란문·
  국화문·여의두문·당초문으로 불리는 문양 상당수가 연화문의 변형이라는
  재해석)를 5개 기준에 "학계 참고" 절로 반영. 단일 학설이므로
  pattern_name 자체는 통용 명칭을 우선 사용하고, alternative_candidate에
  "연화문(...계열)"을 함께 적도록 절충.

---

## v7 - 실사용 사진 테스트에서 발견된 문제 수정 (v6 배포 직후)

- 실제 청자 사진 테스트에서, VLM 한 호출이 서로 다른 두 개의 띠를 하나의
  항목으로 합쳐서 반환하는 사례(bbox 세로 폭이 사진 세로의 36.7%)가
  확인됨. 이 병합된 항목이 3/3 합의로 유일하게 표시되고, 실제 어깨
  띠는 판정보류/추정 동률로 기본 화면에서 숨겨져 "띠가 1개만 있다"는
  식으로 보고서가 나오는 문제. 3중 방어:
  1) 프롬프트에 border_band 세로 폭 20% 제한 규칙 명시
  2) _sanitize_raw_patterns()에 20% 초과 시 강제 분리하는 안전장치 추가
  3) uncertainty 필드의 중복 문장을 pattern_name_signature 기준으로 통합
  4) build_report_sentence의 판정보류 안내에 배지+추정 명칭까지 표시

---

## v6 - 다른 LLM 버전(v5)을 새 베이스로 삼고 6가지 패치

1. alternative_candidate의 문자열 "null"/"None" 등을 실제 null로 정규화
2. pattern_family에 "자연문" 추가, 구름문을 자연문으로 재분류
3. 기본 화면 표시 조건에 decision != "판정보류" 추가
4. border_band는 안전모드와 무관하게 항상 실제 마스킹 사용
5. _tighten_box_to_ink()에 exclusion_mask 인자 추가(다른 문양 영역 침범 방지)
6. border_band 배지를 "상단띠"/"하단띠" 위치 기준으로 표시

---

## v5 이전

- 기본 결과 화면은 문양 중심 마커만 표시, 상세 검토 모드에서만 근사
  bbox/영역 표시.
- RF 유광/무유 라벨을 '표면 광택 수준(높음/낮음)'으로 변환.
- 상단·하단 띠 문양 병합 방지(프롬프트+후처리 이중 방어).
- small_instance 오버사이즈 bbox 제외.
- 로컬 CNN(predict_pottery_multitask.py) 기반 시대 추정 통합.

---

## 검증 방법에 대한 공통 참고

이 프로젝트는 실제 모델(Grounding DINO, SAM2, RF, CNN, OpenAI API)이
설치되지 않은 환경에서 개발이 많이 진행되어, 대부분의 변경은 스텁
(가짜 detector/VLM 응답)으로 로직/제어흐름만 검증했습니다. 실사진
테스트는 사용자가 직접 실행 결과를 공유해준 사례들(용문 청자 등
소수)에 한정됩니다. **정량 평가(라벨링된 정답 대비 정확도)는 아직
표본이 매우 적으므로, 발표/제출 전 다양한 유물 사진으로 한 번 더
직접 확인하는 걸 권장합니다.**
