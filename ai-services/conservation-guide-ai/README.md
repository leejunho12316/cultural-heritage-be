# 보존 가이드 (conservation-guide-ai)
## 초기 개발 환경 설정
1. 가상환경 생성
   cd /ai-service/conservation-guide-ai
   py -m venv .venv

2. 가상환경 활성화
   - Windows(PowerShell): ai-services/conservation-guide-ai/.venv/Scripts/Activate.ps1
   - Windows(cmd): .venv\Scripts\activate.bat
   - macOS/Linux: source .venv/bin/activate

3. 의존성 설치
   pip install -r app/requirements.txt

4. .env 파일 생성 (app/.env, git에는 포함 안 됨 — 각자 발급받은 키 사용)
   OPENAI_API_KEY=발급받은_키_입력


## 실행 - Docker Build & run
1. 보존 AI만
   docker desktop 실행   
   cd ai-services/conservation-guide-ai
   docker build -t ai-service .
   docker run -p 8000:8000 --env-file .env ai-service

2. Spring, 보존 AI, PostGRE docker compose
   docker compose up --build -d (첫 실행시 build도 같이)
   docker compose up -d         (이미 빌드된 이미지 실행)
   docker compose ps            (각 컨테이너 실행 확인)

   docker compose restart conservation-guide-ai (FastAPI 컨테이너 재시작)
   docker compose stop (컨테이너 멈춤)
   docker compose down (컨테이너 삭제)


## API Body 예시

1. /tasks/{task_id}/start 
전체 단계(해체→세척→강화처리→접합→복원) 
   -> relicInfo에 무게/접합면적/재질/처리목적은 백엔드가 검증하지 않는 카테고리 라벨 문자열이라 자유롭게 입력하면 됨
```
{
  "taskName": "청자상감운학문매병 보존처리",
  "taskManager": "이준호",
  "relicInfo": {
    "name": "청자상감운학문매병",
    "material": "연질토기",
    "period": "고려시대",
    "condition": "표면 균열 및 이물질 부착",
    "weight": "중간",
    "bondingArea": "충분함",
    "treatmentPurpose": "전시용"
  },
  "relicPhoto": [],
  "flow": ["disassembly", "cleaning", "reinforcement", "bonding", "restoration"]
}
```

```
{
  "taskName": "청자상감운학문매병 보존처리",
  "taskManager": "이준호",
  "relicInfo": {
    "name": "청자상감운학문매병",
    "material": "연질토기",
    "period": "고려시대",
    "condition": "표면 균열 및 이물질 부착",
    "weight": "중간",
    "bondingArea": "충분함",
    "treatmentPurpose": "전시용"
  },
  "relicPhoto": [],
  "flow": ["disassembly","reinforcement"]
}
```

2. /tasks/{task_id}/resume
-> 같은 task_id로 resume 반복호출.

```
{
 "resume": {
   "checked_ids": ["disassembly-checklist-01","disassembly-checklist-02","disassembly-checklist-03"]
 }
}

{
 "resume": {
   "confirmed_tools": ["disassembly-tools-01","disassembly-tools-02","disassembly-tools-03"]
 }
}

{
 "resume": {
  "completed_step_ids" : ["disassembly-method-01","disassembly-method-02","disassembly-method-03"]
 }
}

{
 "resume": {
  "photo_urls" : ["/desktop/photo1.png", "/desktop/photo112.png"],
  "memo" : "잘됐당"
 }
}
```



3. 세척 단계 /tasks/{task_id}/resume 순서
```
{
 "resume": {
  "use_physical": true,
  "use_chemical": true
 }
}

{
 "resume": {
  "completed_step_ids": ["cleaning-guide-01", "cleaning-guide-02"]
 }
}

{
 "resume": {
  "completed_step_ids": ["cleaning-drying-01", "cleaning-drying-02"]
 }
}

{
 "resume": {
  "photo_urls": ["/desktop/cleaning_after.png"],
  "memo": "세척 완료"
 }
}
```

5. 강화처리 단계 /tasks/{task_id}/resume 순서
-> confirm_wetting_test에서 "retry"를 보내면 강화제/용매 재선택(2-1)으로 되돌아가고, "proceed"를 보내면 다음(2-3)으로 진행됨
```
{
 "resume": {
  "agent": "Paraloid B-72",
  "solvent": "아세톤"
 }
}

-> POST) localhost:8080/photos/upload
BODY - form-data에 - Key는 file File 형식으로, Value는 실제 이미지 입력
{
    "url": "https://conservation-guide-ai-wetting-photos.s3.ap-northeast-2.amazonaws.com/wetting-photos/bf5afe12-8d03-4cd7-8488-6e2aebe77f90-after.png?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Date=20260729T041852Z&X-Amz-SignedHeaders=host&X-Amz-Credential=AKIA33WAIZMZYGVMG7NN%2F20260729%2Fap-northeast-2%2Fs3%2Faws4_request&X-Amz-Expires=3600&X-Amz-Signature=84697a6bce27b0eb0bbe891be422ee9118bc7bbbbcfc1285bde56e4d53ee7ef6"
}




-> 반환받은 URL 입력
{
 "resume": {
  "before_photo_urls": ["https://conservation-guide-ai-wetting-photos.s3.ap-northeast-2.amazonaws.com/wetting-photos/89b03de7-95e3-4d45-a5a0-d802f48a2891-before_%EC%95%9E%EB%A9%B4.jpg?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Date=20260731T062001Z&X-Amz-SignedHeaders=host&X-Amz-Credential=AKIA33WAIZMZYGVMG7NN%2F20260731%2Fap-northeast-2%2Fs3%2Faws4_request&X-Amz-Expires=3600&X-Amz-Signature=9a1e86b7f2054e6a72fb91ef12bc545dd835b58517c862bc3f366ddc94996fe8"],
  "after_photo_urls": ["https://conservation-guide-ai-wetting-photos.s3.ap-northeast-2.amazonaws.com/wetting-photos/933ddcb5-0e55-42de-85c3-9cbeeb8443b6-after_%EC%95%9E%EB%A9%B4.jpg?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Date=20260731T062023Z&X-Amz-SignedHeaders=host&X-Amz-Credential=AKIA33WAIZMZYGVMG7NN%2F20260731%2Fap-northeast-2%2Fs3%2Faws4_request&X-Amz-Expires=3600&X-Amz-Signature=8bdab328f9335f8074e2c2aff0c7b7c6aa2506e9fcf32873da1af2b129791e82"]
 }
}

{
 "resume": {
  "action": "proceed"
 }
}

{
 "resume": {
  "completed_step_ids": ["reinforcement-method-01", "reinforcement-method-02"]
 }
}
```
(2-4 건조 시작 단계는 interrupt가 없어 자동으로 통과되고, 바로 아래 2-5 결과 입력으로 이어짐)
```
{
 "resume": {
  "photo_urls": ["/desktop/reinforcement_after.png"],
  "memo": "강화처리 완료"
 }
}
```

6. 접합 단계 /tasks/{task_id}/resume 순서
```
{
 "resume": {
  "adhesive": "Cemedine C"
 }
}

{
 "resume": {
  "before_photo_urls": ["test_photos/before.png"],
  "after_photo_urls": ["test_photos/after.png"]
 }
}

{
 "resume": {
  "completed_step_ids": ["bonding-method-01", "bonding-method-02"]
 }
}

{
 "resume": {
  "photo_urls": ["/desktop/bonding_after.png"],
  "memo": "접합 완료"
 }
}
```

7. 복원 단계 /tasks/{task_id}/resume 순서
```
{
 "resume": {
  "material": "Araldite SV427+HV427"
 }
}

{
 "resume": {
  "completed_step_ids": ["restoration-guide-01", "restoration-guide-02"]
 }
}

{
 "resume": {
  "photo_urls": ["/desktop/restoration_after.png"],
  "memo": "복원 완료"
 }
}
```


---
#개발 노트

# 7/24~ 강화처리 노드 고도화

## 역할분담
서하, 준호, 한별, 정원

서하 : FE 연동
준호 : 강화처리 (reinforcement)
정원 : 세척 (cleaning)
한별 : 접합 (bonding)


## 강화제 용매 추천
1.LLM 호출에 다음 내용 고려하도록 프롬프트 고도화
1. 유물 재질에 맞는 강화제
2. 추후 재처리가 필요한지 여부
- 전시용이면 강하게, 보관용이면 가역적이게.
3. 토기 무게
4. 접합면 넓이

2. PDF로부터 강화 관련 내용만 뽑아서 



## 습윤 효과
- FE 에서 img 업로드할 수 있도록 하려면?
- 습윤 효과 테스트 VLM 사용시 이미지 경로 잘못됐을 때 재시도할 수 있도록 루프.
- 습윤 효과 테스트시 이전 돌아가기 제대로 작동하는지 확인.



# 7/22 ~ 7/24
완료) 0. 클라우드 deploy push
   작동 잘 되는지 다시 한번 확인하고 PR, merge
완료) 1. 한 그래프로 다 진행해야하는가, 따로 쪼개야하는가? -> 한 그래프
완료) 2. 세척, 강화 처리, 접합, 복원 그래프 역할 문서로 디테일하게 정리
완료) 3. Claude로 노드 복사
완료) 노드 복사 + README에 API 예시 데이터 적어달라하기
완료) 4. 테스트

완료) 5. 기본값
기본값 : 체크를 수동으로 하지 않아도 다음 다음으로 바로 넘어갈 수 있도록 LLM 응답에 기본값 추가.
FE에서는 이 기본값들을 미리 체크된 것으로 인식하고 사람이 굳이 체크하지 않아도 다음 클릭 가능하도록 설계

- 기본값이 필요한 단계
  해체 - 체크리스트 선택 : 도구 추천 recommended: true 되어 있는 도구 기본 체크
  해체 - 작업 후 처리 : 세척법 선택 (need_physical_cleaning, need_chemical_cleaning 값을 기본 체크),
  세척 - 작업 후 처리 : recommended_agent, recommended_solvent를 드랍다운 기본값으로
  강화처리 - 작업 후 처리 : recommended_adhesive을 드랍다운 기본값으로
  접합 - 작업 후 처리 : recommended_material 드랍다운 기본값으로

완료) 6. 전체 완료 버튼
기본값을 설정할 수 없는 단계의 경우 FE에 전체 완료 버튼을 만들어 빠른 스킵이 가능하도록 설계.

- '전체 완료 버튼'이 필요한 단계
  해체: 체크리스트 선택, 단계별 작업 안내
  세척: 단계별 작업 안내, 건조 단계별 작업 안내
  강화처리: 단계별 작업 안내
  접합: 단계별 작업 안내
  복원: 단계별 작업 안내

완료) 7. 드랍다운 값 명확히

   강화제
   Literal["Paraloid B72", "HPC", "폴리비닐부티랄", "수용성 Emulsion", "Paraloid NAD-10"]

   용제 (5개 강화제의 전체 허용 용매를 합친 목록)
   Literal["아세톤", "톨루엔", "자일렌", "에틸아세테이트", "이소프로판올", "에탄올", "MEK", "아밀아세테이트", "메탄올", "물", "나프타",  "화이트스피릿"]
   
   접착제                                                                      
   Literal["Paraloid B-72", "Cemedine C", "Araldite rapid", "Cyanoacrylate", "poly urethane", "Loctite 401"]
   
   복원제
   Literal["CDK-520", "Araldite SV427+HV427", "Epo-tec 301", "XTR-311", "Repairit Quik"]


완료) 8. API 명세서, Figma 편집









4. 전체 노드 작동 확인 후 API 명세서 전체 정리.

```
세척/강화처리/접합/복원 4단계 노드 추가                                                                                                                                                             
                                                                                                                                                                                                       
   Context                                                                                                                                                                                             
                                                                                                                                                                                                       
   현재 ai-services/conservation-guide-ai는 해체(disassembly) 한 단계만 구현되어 있고, 실제 보존처리 흐름은 해체 → 세척 → 강화처리 → 접합 → 복원 → (색맞춤, 추후)까지 있음. 이번 작업은                
   세척/강화처리/접합/복원 4단계를 disassembly와 동일한 패턴(LangGraph 노드 + interrupt 기반 human-in-the-loop)으로 추가하는 것. EC2 배포에서 이미 disassembly의 interrupt/resume/PostgresSaver 조합이 
   검증되었으므로, 그 검증된 패턴을 최대한 그대로 재사용하는 방향으로 설계함.                                                                                                                          
                                                                                                                                                                                                       
   확정된 결정사항                                                                                                                                                                                     
                                                                                                                                                                                                       
   1. 그래프 구조: 진짜 nested subgraph 대신 flat 그래프 + 공통 체인빌더 헬퍼로 감. 이유: interrupt/resume이 이미 flat 구조에서 검증됨. subgraph는 stage_guard를 스테이지 진입 조건부 엣지로           
   재설계해야 하고 interrupt가 subgraph 경계를 넘는 조합을 새로 검증해야 하는 리스크가 있음.                                                                                                           
   2. 습윤효과 테스트(강화처리 2-2): 실제 VLM 자동 분석으로 구현. relic_photo/각 스테이지 결과 사진이 이미 URL 문자열 리스트로 다뤄지고 있으므로(photo_urls 컨벤션), base64 인코딩 없이 image_url      
   콘텐츠 타입으로 vision 모델에 바로 전달 가능. 새 패키지 설치 불필요 (langchain-openai의 ChatOpenAI가 멀티모달 메시지를 이미 지원).                                                                  
   3. 무게/접합면적 등 범위값: 백엔드는 숫자 범위를 검증하지 않고, FE가 보내는 카테고리 라벨 문자열을 그대로 relic_info에 담아 LLM 프롬프트 컨텍스트로 전달만 함. 같은 원칙을                          
   재질(연질토기/경질토기/도기/석기/유리화된 자기 등), 처리목적(전시용/수장연구용/기타 자유입력)에도 동일하게 적용 — 전부 relic_info: dict 안의 자유 문자열 키로 취급하고 backend는 enum 검증을 하지   
   않음.                                                                                                                                                                                               
                                                                                                                                                                                                       
   아키텍처                                                                                                                                                                                            
                                                                                                                                                                                                       
   노드 패턴 (기존 nodes/disassembly.py 그대로 재사용)
   
    - _get_xxx() private 함수: llm.with_structured_output(SomeSchema)로 LLM 호출, assign_ids()로 결과에 {major}-{minor}-{순번} ID 부여                                                                  
   - LLM 호출 노드와 interrupt() 노드를 분리 (resume 시 재실행돼도 LLM이 중복 호출되지 않도록) — 이 규칙을 새 4단계에도 동일 적용                                                                      
   - 모든 노드에 @stage_guard("stage_name") 데코레이터 적용 (flow에 없으면 skip)                                                                                                                       
                                                                                                                                                                                                       
   graph.py 리팩터링                                                                                                                                                                                   
                                                                                                                                                                                                       
   현재 disassembly 체인을 그대로 나열하던 부분을 헬퍼로 추출:                                                                                                                                         
   def add_linear_stage(builder, node_chain: list[str], node_funcs: dict):                                                                                                                             
       for name in node_chain:                                                                                                                                                                         
           builder.add_node(name, node_funcs[name])                                                                                                                                                    
       for prev, nxt in zip(node_chain, node_chain[1:]):                                                                                                                                               
           builder.add_edge(prev, nxt)                                                                                                                                                                 
   disassembly, cleaning, bonding, restoration은 이 헬퍼로 순수 선형 체인을 등록. 스테이지 간 연결은 add_edge(마지막_노드, 다음스테이지_첫_노드)로 이어붙임 (disassembly_end → cleaning_checklist →    
   ... → restoration_end → END).                                                                                                                                                                       
                                                                                                                                                                                                       
   예외: reinforcement는 순수 선형이 아님. 2-2 습윤효과테스트 결과가 "색변화 심함"이면 2-1(강화제/용매 재선택)로 되돌아가야 함. 이 부분만 헬퍼 적용 후 별도로                                          
   builder.add_conditional_edges("reinforcement_confirm_wetting_test", route_fn, {"retry": "reinforcement_agent_solvent", "proceed": "reinforcement_method"}) 추가.
   
    llm.py

    기존 텍스트 전용 llm 옆에 vision 지원 모델 인스턴스 추가:
    vision_llm = ChatOpenAI(model="gpt-4o", temperature=0, api_key=os.environ["OPENAI_API_KEY"])
    (정확한 모델명은 실제 vision 품질 테스트 후 조정 가능)
   
    state.py
   
    TypedDict 구조 변경 불필요 — relic_info: dict가 이미 범용이라 무게/접합면적/재질/처리목적 카테고리 라벨을 그 안에 자유 키로 담으면 됨. results: Annotated[dict, merge_results] reducer도 그대로 재사용되어 스테이지별 결과가 자동 병합됨.
   
    스테이지별 노드 구성 (representative, disassembly 7노드 패턴과 동일 스타일)
   
    세척 (nodes/cleaning.py)
    - 1-1 cleaning_analysis (LLM: 유물상태/오염물 요약 + 물리적/화학적 세척 필요여부 분석) → cleaning_confirm_method (interrupt: 어떤 세척법 진행할지 FE 체크)
    - 1-2 cleaning_guide (LLM: 체크된 세척법만 단계별 안내) → cleaning_confirm_guide (interrupt: 단계 완료 체크)
    - 1-3 cleaning_drying_guide (LLM: 재질에 따른 건조법 안내) → cleaning_confirm_drying (interrupt: 완료 체크)
    - 1-4 cleaning_end (interrupt: 사진/메
   강화처리 (nodes/reinforcement.py)
    - 2-1 reinforcement_agent_solvent (LLM: 강화제+용매 추천+이유) → reinforcement_confirm_agent (interrupt: FE 드롭다운 선택)
    - 2-2 reinforcement_wetting_test (VLM: 이전/이후 테스트 사진 색변화 분석, vision_llm 사용) → reinforcement_confirm_wetting_test (interrupt: 진행/돌아가기 선택) → 조건부 엣지로 2-1 또는 2-3
    - 2-3 reinforcement_method (LLM: 분무법/침지법 추천+단계안내) → reinforcement_confirm_method (interrupt: 완료 체크)
    - 2-4 reinforcement_dry_start (LLM 없음, dry_start_time 기록만 하고 바로 다음으로 — interrupt 없음)
    - 2-5 reinforcement_end (interrupt: 사진/메모)
   
    접합 (nodes/bonding.py)
    - 3-1 bonding_adhesive (LLM: 무게/접합면적/재질/처리목적/강화제 종류 참고해 접착제 추천) → bonding_confirm_adhesive (interrupt: FE 6종 중 선택)
    - 3-2 bonding_temp (interrupt만: 임시접합 전/후 사진, LLM 없음)
    - 3-3 bonding_method (LLM: 복합/단일/결합/모세관접합 중 추천+단계안내) → bonding_confirm_method (interrupt: 완료 체크)
    - 3-4 bonding_end (interrupt: 사진/메모)
   
    복원 (nodes/restoration.py)
    - 4-1 restoration_material (LLM: 5종 합성수지 중 추천+이유) → restoration_confirm_material (interrupt: FE 선택)
    - 4-2 restoration_guide (LLM: 단계별 안내) → restoration_confirm_guide (interrupt: 완료 체크)
    - 4-3 restoration_end (interrupt: 사진/메모)
    
     schemas.py 추가 모델
   
    기존 DisassemblyChecklist/ToolRecommendation/DisassemblyMethod 3가지 형태(체크리스트형, 추천+이유형, 단계별안내형)를 각 스테이지에 재사용:
    - 체크리스트/분석형: CleaningAnalysis
    - 추천형: ReinforcementAgentRecommendation, BondingAdhesiveRecommendation, RestorationMaterialRecommendation
    - 단계안내형: CleaningGuide, DryingGuide, ReinforcementMethod, BondingMethodRecommendation, RestorationGuide
    - 신규 VLM 전용: ColorChangeAnalysis (ABSA 스타일 9개 항목: hue_shift/brightness_change/saturation_change/gloss_change/blanching/uneven_penetration/edge_visibility/crack_response/texture_change — 각 AspectResult(severity: none/mild/moderate/severe, description) 고정 필드 + overall_severity/recommendation 종합판정)
   
    수정/추가 파일 목록
   
    - app/nodes/cleaning.py, app/nodes/reinforcement.py, app/nodes/bonding.py, app/nodes/restoration.py (신규)
    - app/schemas.py (모델 추가)
    - app/llm.py (vision_llm 추가)
    - app/graph.py (add_linear_stage 헬퍼 추출 + disassembly 리팩터링 + 4단계 등록 + reinforcement 조건부 엣지)
    - Spring 쪽(src/main/java/.../conservation_guide_ai/)은 flow가 이미 List<String>이라 코드 변경 불필요
    
    검증 방법
   
    1. docker-compose up --build로 로컬 재기동
    2. Postman으로 flow: ["disassembly","cleaning","reinforcement","bonding","restoration"] 전체 포함해 /tasks/{id}/start 호출 → 각 interrupt 지점마다 /resume으로 순서대로 진행하며 세척→강화처리→접합→복원까지 전체 흐름 확인
    3. 강화처리 2-2에서 색변화 "심함"으로 답하는 케이스를 만들어 2-1로 정상적으로 되돌아가는지(조건부 엣지) 별도 확인
    4. flow에 일부 단계만 포함시켜 (예: ["disassembly","bonding"]) 나머지 단계가 stage_guard에 의해 정상 skip 되는지 확인
```



# 7/21 Cloud 배포화
EC2 인스턴스 1대에 Docker Compose (Spring + FastAPI + Postgres 컨테이너 3개 한 인스턴스에서 실행)
1. Spring용 Dockerfile 추가 & docker-compose.yml 작성
   Dockerfile, .dockerignore, docker-compose.yml
2. LangGraph 체크포인터 SqliteSaver -> PostgresSaver
3. application.yaml base-url 값을 환경변수에서 받아오도록. (application.yaml의 AI 서비스 base-url 환경변수)

AWS Cloud 시작
1. EC2 인스턴스 만들기
- AMI: Ubuntu 22.04 LTS
- 인스턴스 타입: t2.micro는 컨테이너 3개 돌리기엔 부족해서 t3.small/medium 권장
- 보안 그룹: 실제로 외부에서 접근해야 하는 8080(Spring)만 열고, 8000(FastAPI)/5432(Post

2. EC2 인스턴스 docker, docker compose 설치
userdata
```
#!/bin/bash                                                                                                                                                                                            
exec > >(tee /var/log/user-data.log) 2>&1

apt update                                                                                                                                                                                             
apt upgrade -y

curl -fsSL https://get.docker.com -o get-docker.sh                                                                                                                                                     
sh get-docker.sh

usermod -aG docker ubuntu
```
docker --version
docker compose version

3. 작업하던 프로젝트 git에 push 후 EC2 인스턴스에 SSH로 접속해서 clone.
   -> .env 생성 & GitHub 인증 Access Token (Classic)
   ```nano .env``
   -> git checkout feature/cloud-deploy

4. EC2 인스턴스에서 docker compose up --build -d

5. API 테스트 - POST http://<EC2퍼블릭IP>:8080/tasks/{taskId}/start



# 7/20
MVC 패턴에 맞게 폴더 정리
통신 방식별로 DTO 더 나누기.
전체 노드 출력 통일
API 명세서 작성.


# 7/17~19
LangGraph 사용 notebook 파일을 /app에 기능별로 python 파일로 쪼개 작성.
main.py에 FastAPI로 LangGraph 사용하는 API 2개(start, resume) 작성
ai-service 모듈이 .venv python 가상환경을 사용해 안에 python 파일들을 실행하도록 환경설정.

__init__.py : 폴더를 패키지로 인식하도록 도와주는 파일. LangGraph 기능을 쪼개서 여러 python 파일로 옮겼는데, 서로를 상대경로로 인식하기 위해서는 /app과 /app/nodes를 패키지로 인식해야함.


1단계: Python 코드를 "노트북 스크립트"에서 "모듈"로 정리 (완료)
- ai-services/conservation-guide-ai/app에 notebook 파일로 만든 LangGraph 코드를 State, disassembly, graph 등 역할별로 분리.
- requirements.txt 작성


2단계: FastAPI 래퍼 작성 
main.py 작성
- POST /tasks/{task_id}/start: graph.invoke(initial_state, config={"configurable":{"thread_id": task_id}}) 실행 → __interrupt__ 있으면 그 payload를 JSON으로 반환, 없으면 완료 결과 반환
- POST /tasks/{task_id}/resume: body로 받은 값을 graph.invoke(Command(resume=body), config=...)에 전달 → 다음 interrupt 또는 완료 결과 반환
- 이 두 엔드포인트는 범용(generic) 이어야 함 — 나중에 노드를 추가/삭제하거나 RAG를 넣어도 이 계약(request/response 모양)은 안 바뀌게 설계하는 게 핵심.

3단계: checkpointer를 프로덕션용으로 교체 (완료)
- LangGraph 상태 저장 메모리 교체 (sqlite3.connect(":memory:") -> SQLite(checkpoints.db))

3.5단계: git 업로드
https://github.com/BigProject09/cultural-heritage-be

4단계: Python 서비스 Dockerize
- ai-service/ 디렉터리에 requirements.txt, Dockerfile 작성 (uvicorn으로 FastAPI 구동)
- checkpoint DB 파일용 볼륨 경로 설정

4.5단계: node별 출력 고도화
node별 interrupt()값, return 값 구체화. 
최종 정리 node 추가.

5단계: Spring 쪽 클라이언트 작성
- application.yaml에 ai-service.base-url 같은 프로퍼티 추가
- RestClient(또는 WebClient) Bean 설정
- FastAPI 응답을 받을 DTO (예: AiTaskResponse — interrupt 여부, payload, 완료 상태 등을 담는 제네릭 구조)
- AiServiceClient 클래스: startTask(taskId, initialState), resumeTask(taskId, resumeValue) 메서드
- Spring 컨트롤러/서비스에서 이 클라이언트를 호출해 프론트엔드에 응답 전달, 필요한 비즈니스 데이터(담당자, 유물 정보 등)는 Spring 자체 DB(JPA/H2)에도 저장

ㅇ) 5-1. application.yaml에 AI 서비스 주소 등록                                                                                                                                                              
conservation-guide-ai:                                                                                                                                                                                 
   base-url: http://localhost:8000                                                                                                                                                                      
   나중에 docker-compose로 묶이면 이 값만 http://ai-service이름:8000으로 바뀌면 되니, 코드에 하드코딩하지 않고 프로퍼티로 뺍니다.

ㅇ) 5-2. common/config/RestClientConfig.java — RestClient Bean 등록
- application.yaml의 conservation-guide-ai.base-url을 @Value로 주입받아 RestClient.builder().baseUrl(...).build() Bean 하나 생성

ㅇ) 5-3. common/ai/AiTaskResponse.java — 응답 DTO
- FastAPI _format_response가 주는 {"status": "waiting_for_input"/"completed", "interrupt": {...}, "result": {...}} 구조를 그대로 받는 DTO
- interrupt/result는 구조가 단계마다 달라지니 Map<String, Object> 정도로 느슨하게 받는 게 안전함 (나중에 X-RAY 등 다른 서비스도 같은 DTO 재사용 가능)

ㅇ) 5-4. conservation_guide_ai 패키지에 요청 DTO
- StartTaskRequest (task_name, task_manager, relic_info, relic_photo, flow — FastAPI의 StartTaskRequest랑 필드 맞추기)
- ResumeTaskRequest (resume: Map<String, Object>)

ㅇ) 5-5. ConservationGuideAiClient.java — 실제 호출 로직
- startTask(taskId, StartTaskRequest) → POST /tasks/{taskId}/start
- resumeTask(taskId, ResumeTaskRequest) → POST /tasks/{taskId}/resume
- 둘 다 AiTaskResponse 반환

ㅇ?) 5-6. ConservationGuideAiController.java — 프론트엔드에 노출할 엔드포인트
- Client를 호출해서 결과를 그대로(or 가공해서) 프론트엔드에 전달

ㅇ) 5-7. 테스트
- 로컬 uvicorn(http://localhost:8000) 띄워둔 상태에서 Spring 켜고, Postman/curl로 Spring 엔드포인트 호출 → FastAPI까지 잘 이어지는지 확인

-> DTO, Client, Server 등 폴더 나누기
-> 파일 이름 알아볼 수 있게 바꾸기 (**DTO, **Client)


6단계: docker-compose로 통합
- docker-compose.yml에 spring-backend, ai-service 두 서비스 정의, 내부 네트워크로 연결 (Spring이 http://ai-service:8000 호출)

7단계: 통합 테스트

- Spring → /start → interrupt payload 수신 → 프론트 확인 시나리오 흉내 → /resume 호출까지 엔드투엔드로 확인

---지금 바로 시작할 수 있는 건 1단계(코드 정리)와 2단계(FastAPI 래퍼)인데, 어디부터 실제로 작업 들어갈까요? 아니면 전체를 순서대로 쭉 진행할까요?