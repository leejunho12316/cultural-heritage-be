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
# 7/21 Cloud 배포화
EC2 인스턴스 1대에 Docker Compose (Spring + FastAPI + Postgres 컨테이너 3개 한 인스턴스에서 실행)
1. Spring용 Dockerfile 추가 & docker-compose.yml 작성
   Dockerfile, .dockerignore, docker-compose.yml
2. LangGraph 체크포인터 SqliteSaver -> PostgresSaver
3. application.yaml base-url 값을 컨테이너 네트워크 기준 값으로 분리. (application.yaml의 AI 서비스 base-url 환경변수)

AWS Cloud 시작
1. EC2 인스턴스 만들기
2. 작업하던 프로젝트 git에 push 후 EC2 인스턴스에 접속해서 clone.
-> .env 생성 & GitHub 인증 Access Token (Classic)
3. EC2 인스턴스에서 docker compose up --build -d
4. API 테스트 - POST http://<EC2퍼블릭IP>:8080/tasks/{taskId}/start



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