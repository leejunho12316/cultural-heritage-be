cd ai-services/conservation-guide-ai

# 1. 평가 전용 Vector DB 구축 (eval_original_pdfs/ → eval_vector_store/)
python -m HyDE_evaluation.build_vector_db

# 2. 문헌에서 Golden Set 자동 추출 → 검수
python -m HyDE_evaluation.build_golden_set

python -m HyDE_evaluation.build_golden_set --top-k 40 --batch 5

VectorDB로부터 강화제/용매 관련한 12개의 쿼리로 각각 검색. 
각 검색마다 top-k개씩만 사용하는데, batch 개수만큼 묶어서 한 번에 LLM에 넣어 Golden Set 생성.

# 3. golden_set.json 검수 및 수정

# 4. 평가 실행
python -m HyDE_evaluation.runner --k 5

# 5. LLM-as-judge 채점
python -m HyDE_evaluation.judge

# 6. 리포트 출력
python -m HyDE_evaluation.metrics

구현 요약:
- config.py — sys.path 처리 포함, 프로덕션과 분리된 경로 상수
- build_vector_db.py — eval_original_pdfs/ PDF → eval_vector_store/ Chroma (text-embedding-3-small, chunk 800/100)
- build_golden_set.py — 벡터 DB 전체 청크에서 LLM이 재질→강화제+용매 추출, 중복 제거 후 golden_set.json 저장
- callers.py — retrieve_plain_rag / retrieve_hyde_rag / call_recommendation (프로덕션 프롬프트 그대로)
- runner.py — Golden Set 기반 K회 반복, plain/HyDE 각각 결과 저장
- judge.py — 검색 품질(relevance) + 답변 전문성(professionalism) + 문헌 반영도(groundedness) LLM 채점
- metrics.py — 3개 섹션(Golden Set 정확도/검색 품질/답변 품질) 리포트 출력

# TroubleShooting

1. (완료) 특정 강화제를 용해하는데 쓸 수 있는 옹매는 다양함. Golden Set과 비교했을 때 강화제는 맞췄는데 용매가 다르다고 해서 틀린게 아닐 수 있음.
그래서 아래의 쌍에 해당이 되면 정답으로 하는게 맞지 않을까?

### 강화제 드랍다운 값
Paraloid B72, HPC, 폴리비닐부티랄, 수용성 Emulsion, Paraloid NAD-10

### 용제 드랍다운 값
아세톤, 톨루엔, 자일렌, 에틸아세테이트, 이소프로판올, 에탄올, MEK, 아밀아세테이트, 메탄올, 물, 나프타,  화이트스피릿

### 강화제별 허용 용제

- Paraloid B-72 (아크릴계)
  대표 용매 : 아세톤
  사용 가능한 기타 용매 : 톨루엔, 자일렌, 에틸아세테이트(초산에틸), 이소프로판올, 에탄올(부분 용해·보조용), MEK, 아밀아세테이트
  사용 불가/비권장 : 화이트 스피릿, 나프타

- HPC
  대표 용매 : 에탄올 또는 물
  사용 가능한 기타 용매 : 메탄올, 이소프로판올(IPA), 아세톤
  사용 불가/비권장 : 뜨거운 물, 벤젠, 에테르, 나프타, 화이트 스피릿

- 폴리비닐부티랄
  대표 용매 : 에탄올
  사용 가능한 기타 용매 : 알코올류, 아세톤, 방향족 탄화수소(톨루엔 등)
  사용 불가/비권장 : 물

- 수용성 에멀전
  대표 용매 : 물
  사용 가능한 기타 용매 : -
  사용 불가/비권장 : 아세톤, 톨루엔

- Paraloid NAD-10
  대표 용매 : 나프타
  사용 가능한 기타 용매 : 화이트 스피릿
  사용 불가/비권장 : 물, 극성 용매

2. (완료) Golden Set
VectorDB로부터 Golden Set을 만들 때, relic_info에 해당 chunk에 있는 상세한 내용들을 텍스트로 추가해주면 RAG에 더 도움이 되지 않을까?
FE에서는 유물의 '현재 상태'를 긴 텍스트로 description처럼 입력받을 수 있게 해놓았었는데, 이걸 자동으로 채워주면 좋을 것 같음.

3. (완료) Golden Set 2
Golden Set 만들 때 VectorDB에 질문하는 쿼리가 제대로 안되있는거 아님?
-> 쿼리 종류별로 많이 추가

4. Golden Set 개수
개수 늘리는 방법
- pdf 더 구하기
- LLM으로 유사 사례 제작하기


---

5. LLM-as-a-Judge
LLM-as-a-Judge 방식을 RAGAS 방식으로 바꾸는 건 어떨까?
RAGAS에서 평가하는 3가지 항목을 평가하는 것은 RAG 평가에 아주 좋을 듯.