# 실행방법
cd C:/Users/Hane/IdeaProjects/cultural-heritage-be/ai-services/conservation-guide-ai

### 1. 테스트 데이터 생성 (eval_original_photos → eval_test_photos)
X) python -m ABSA_evaluation.generate_dataset --count 10
python -m ABSA_evaluation.generate_dataset --variants-per-image 10

### 2. 평가 실행 (K=5, gpt-5.5, temperature=0.7)
python -m ABSA_evaluation.runner --k 5

- k : 쌍 하나당 baseline k회 + ABSA k회 = 총 2k회 평가 호출

### 3. 근거 텍스트 일관성 채점
python -m ABSA_evaluation.judge

- severity와 함꼐 나오는 답변 근거 text의 일관성을 LLM-as-a-Judge 방식으로 체크.

### 4. 결과 리포트
python -m ABSA_evaluation.metrics

- severity 포함 모든 metric 리포트 출력


# 결과
기존에는 자유로운 description + 심각도 출력.
하지만 이러면 사용자에게 일관된 답변 제공이 불가능. 그래서 ABSA 방식을 응용해서 9개의 속성을 4개의 단계로 추출하게 함.
추출 속성이 많아질수록 답변 일관성이 낮아지는 문제 발생. 하지만 기존 방식은 하나의 칼럼을 4단계 중에서 맞추면 되니 높은거라 허수라고 판단.
또한 description 텍스트 품질 LLM-as-a-Judge 평가를 진행했을 때 ABSA 방식이 기존 방식과 비교해 정보의 다양성 면에서 점수가 17.2% 높아짐.


=================================================================
Reinforcement Wetting — VLM Consistency & Accuracy Report
Results: C:\Users\Hane\IdeaProjects\cultural-heritage-be\ai-services\conservation-guide-ai\ABSA_evaluation\results   Pairs: 330
=================================================================

--- Severity 값 평가 ---

Metric                                 Baseline              ABSA (9-aspect mean)
-----------------------------------------------------------------
일관도 (Consistency)                         86.2%                     80.8%   -5.4pp
Ground Truth (정답) 기반 정확도 (n=330)          41.2%                 29.0%   -12.2pp

--- description 텍스트 품질 (LLM Judge, 1~5점) ---

Metric                           Baseline       ABSA (9-aspect mean)
-----------------------------------------------------------------
일관성 (consistency)                  3.70/5                  3.83/5   +0.13pt
관점 다양성 (richness)                  3.90/5                4.57/5   +0.67pt

--- Per-Aspect ABSA Detail (across all pairs) ---
hue_shift                 consistency: 85.3%   정답 기반 정확도: 44.0%
brightness_change         consistency: 71.3%   정답 기반 정확도: 27.3%
saturation_change         consistency: 77.3%   정답 기반 정확도: 16.0%
gloss_change              consistency: 80.0%   정답 기반 정확도: 24.7%
blanching                 consistency: 92.7%   정답 기반 정확도: 37.3%
uneven_penetration        consistency: 75.3%   정답 기반 정확도: 25.3%
edge_visibility           consistency: 72.0%   정답 기반 정확도: 32.0%
crack_response            consistency: 84.0%   정답 기반 정확도: 44.0%
texture_change            consistency: 76.0%   정답 기반 정확도: 36.7%

overall_severity  consistency: 90.7%   정답 기반 정확도: 52.0%
=================================================================

