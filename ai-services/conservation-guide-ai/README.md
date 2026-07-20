# 보존 가이드 (conservation-guide-ai)
## 초기 개발 환경 설정
1. 가상환경 생성
   cd /ai-service 
   py -m venv .venv

2. 가상환경 활성화
   - Windows(PowerShell): ai-services/conservation-guide-ai/.venv/Scripts/Activate.ps1
   - Windows(cmd): .venv\Scripts\activate.bat
   - macOS/Linux: source .venv/bin/activate

3. 의존성 설치
   pip install -r app/requirements.txt

5. .env 파일 생성 (app/.env, git에는 포함 안 됨 — 각자 발급받은 키 사용)
   OPENAI_API_KEY=발급받은_키_입력

## 실행 방식 2가지
1. 서버 실행 (ai-service/ 디렉터리에서)
   uvicorn app.main:app --reload
   → http://127.0.0.1:8000/docs 에서 Swagger UI로 /start, /resume 테스트 가능

2. Docker Build & 실행
   docker desktop 실행

   cd ai-services/conservation-guide-ai
   docker build -t ai-service .
   docker run -p 8000:8000 --env-file .env ai-service

.dockerignore에 .env가 있어도 Dockerimage buld후 run 할 때 --env-file .env 로 키 받기 때문에 정상실행 가능.

## swagger 테스트
127.0.0.1:8000/docs 

테스트 데이터 - Postman

1. /tasks/{task_id}/start
-> 처음 한 번만 실행
```
{
  "taskName": "청자상감운학문매병 보존처리",
  "taskManager": "이준호",
  "relicInfo": {"name": "청자상감운학문매병", "material": "도자기", "period": "고려시대", "condition": "표면 균열 및 이물질 부착"},
  "relicPhoto": [],
  "flow": ["disassembly"]
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












---


# 7/17~ 한 일
LangGraph 사용 notebook 파일을 /app에 기능별로 python 파일로 쪼개 작성.
main.py에 FastAPI로 LangGraph 사용하는 API 2개(start, resume) 작성
ai-service 모듈이 .venv python 가상환경을 사용해 안에 python 파일들을 실행하도록 환경설정.

__init__.py : 폴더를 패키지로 인식하도록 도와주는 파일. LangGraph 기능을 쪼개서 여러 python 파일로 옮겼는데, 서로를 상대경로로 인식하기 위해서는 /app과 /app/nodes를 패키지로 인식해야함.


0단계 — 선행 조치 (완료)
- 유출된 OpenAI API 키 폐기 및 재발급 (35번째 줄에 하드코딩된 키). 이건 구조 잡기 전에 먼저 처리해야 합니다.
- 새 키는 .env 또는 환경변수로만 관리하고 코드/레포에는 절대 넣지 않기.

1단계 — Python 코드를 "노트북 스크립트"에서 "모듈"로 정리 (완료)

지금 파일은 # %% 셀 구분, !pip install, 맨 아래 input()으로 사용자 입력받는 데모 실행 코드가 섞여 있어 그대로 임포트해서 쓸 수 없습니다.
- !pip install 줄 제거 → requirements.txt로 분리
- 맨 아래 "temp_state로 실행" ~ 데모 실행 블록(graph.invoke, input() 등) 전부 제거 — 이건 노트북 테스트용이고 서비스 코드가 아님
- State, StageResult, stage_guard, 각 노드 함수, builder/graph 조립 로직을 역할별 파일로 분리 (예: state.py, nodes/disassembly.py, graph.py)

2단계 — FastAPI 래퍼 작성 (완료)

- POST /tasks/{task_id}/start: graph.invoke(initial_state, config={"configurable":{"thread_id": task_id}}) 실행 → __interrupt__ 있으면 그 payload를 JSON으로 반환, 없으면 완료 결과 반환
- POST /tasks/{task_id}/resume: body로 받은 값을 graph.invoke(Command(resume=body), config=...)에 전달 → 다음 interrupt 또는 완료 결과 반환
- 이 두 엔드포인트는 범용(generic) 이어야 함 — 나중에 노드를 추가/삭제하거나 RAG를 넣어도 이 계약(request/response 모양)은 안 바뀌게 설계하는 게 핵심.

3단계 — checkpointer를 프로덕션용으로 교체 (완료)

- 지금 sqlite3.connect(":memory:")는 프로세스 재시작 시 전부 날아감 → 파일 기반 SQLite(checkpoints.db, 볼륨 마운트) 또는 Postgres로 교체.

-> 3.5단계 - git 업로드 (완료)
https://github.com/BigProject09/cultural-heritage-be


4단계 — Python 서비스 Dockerize (완료)

- ai-service/ 디렉터리에 requirements.txt, Dockerfile 작성 (uvicorn으로 FastAPI 구동)
- checkpoint DB 파일용 볼륨 경로 설정


-> 4.5단계 - node별 출력 고도화 (완료)
node별 interrupt()값, return 값 구체화. 
최종 정리 node 추가.


5단계 — Spring 쪽 클라이언트 작성

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


6단계 — docker-compose로 통합

- docker-compose.yml에 spring-backend, ai-service 두 서비스 정의, 내부 네트워크로 연결 (Spring이 http://ai-service:8000 호출)

7단계 — 통합 테스트

- Spring → /start → interrupt payload 수신 → 프론트 확인 시나리오 흉내 → /resume 호출까지 엔드투엔드로 확인

---지금 바로 시작할 수 있는 건 1단계(코드 정리)와 2단계(FastAPI 래퍼)인데, 어디부터 실제로 작업 들어갈까요? 아니면 전체를 순서대로 쭉 진행할까요?