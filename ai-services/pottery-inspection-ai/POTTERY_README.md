# 도자기 육안조사 AI — 실행 가이드

## 0. 시작 전에 꼭 확인할 것

- [ ] Docker Desktop 실행 중인지
- [ ] BE 레포에서 `feature/pottery-inspection-ai` 브랜치인지 (`git branch`로 확인.
      아직 main에 머지 전이면 브랜치 안 맞으면 코드 자체가 안 보임)
- [ ] FE 레포도 마찬가지로 브랜치 확인
- [ ] **`.env`에 `VITE_ARTIFACT_STORAGE_MODE`가 `api`로 설정돼 있는지 확인**
      → 이게 켜져 있으면 `/api/artifacts` BE 엔드포인트를 호출하는데,
      이 엔드포인트가 아직 구현 안 됐을 수 있음(확인 필요). 켜져 있고
      실제로 없으면 화면 진입하자마자 "유물 정보 조회 실패" 알림부터
      뜰 수 있음. 값이 없거나 `local`이면 문제없음.

## 1. 사전 준비

**`.env` 파일** (BE 레포 루트)
```
OPENAI_API_KEY=<발급받은 키>
POSTGRES_PASSWORD=<아무 문자열, 로컬 전용이라 뭐든 상관없음>
AWS_REGION=ap-northeast-2
AWS_ACCESS_KEY_ID=dummy
AWS_SECRET_ACCESS_KEY=dummy
AWS_S3_BUCKET=dummy-bucket
```
AWS 값은 현재 이 기능이랑 무관해서 더미값이어도 됨(경고 로그만 뜨고 정상 동작).

`ai_hub` 폴더(시대 판정용 CNN 모델)는 git에 그대로 커밋돼 있어서, 브랜치
받으면 별도로 챙길 것 없이 자동으로 같이 옴.

## 2. 실행

```bash
docker compose up --build
```

**포트 정리**
| 서비스 | 컨테이너 내부 | 호스트(로컬) |
| --- | --- | --- |
| conservation-backend | 8080 | 8080 |
| pottery-inspection-ai | 8000 | 8003 |
| conservation-guide-ai | 8000 | 8000 |
| xray-ai | 8000 | 8001 |
| postgres | 5432 | (외부 노출 안 함) |

FE는 별도로:
```bash
npm install   # package.json 바뀐 게 있을 수 있음
npm run dev
```
5174번 고정(`vite.config.js`에 `strictPort: true`). 그 포트가 이미 쓰이고
있으면 뜨다가 실패하니, 다른 Vite 프로젝트 켜져 있으면 먼저 끄기.

## 3. 확인 방법

1. 유물 등록 화면 → **재질을 연질토기 / 경질토기 / 도자기 / 백자 / 청자 /
   분청사기 / 옹기 중 하나로 선택.** (이 키워드가 재질명에 포함 안 되면
   AI 안 켜지고 "준비 중" 안내만 뜸 — 정상 동작이지 버그 아님)
2. 육안 상태 조사 화면 진입 → 자동으로 분석 시작
3. **CPU로 돌아가서 느림.** 문양까지 확인하는 경우 최대 1분 정도 걸릴 수
   있음 — 멈춘 것처럼 보여도 좀 기다려볼 것
4. 결과 화면에서 확인할 것: 문양 위치 박스, 등록 시대 vs AI 재분석 시대
   비교 배지, 사진 확대/축소

## 4. 트러블슈팅

**`password authentication failed for user "conservation"`**
→ 오늘 우리가 겪었던 그 문제. 새 환경이라 Postgres 볼륨이 이전 비밀번호를
기억하고 있어서 남. 해결:
```bash
docker compose down
docker volume ls              # pgdata 들어간 볼륨 이름 확인
docker volume rm <그 볼륨 이름>
docker compose up -d
```

**`502 Bad Gateway`**
→ `docker ps`로 `conservation-backend` 컨테이너가 실제로 떠있는지 먼저
확인. 없으면 `docker compose logs conservation-backend --tail 100`으로
원인 확인.

**`ERR_MODULE_NOT_FOUND` (`npm run dev` 시)**
→ `npm install` 다시 (package.json에 새 의존성 추가됐을 수 있음).

**OpenAI 관련 에러(quota, rate limit)**
→ VLM 호출이 많은 기능(문양 위치 + 상태조사 + 시대 근거)이라 크레딧
소진 시 발생 가능. `pottery_api.py`가 원인 메시지를 그대로 반환하니
에러 메시지 확인.

## 5. 참고 문서

- `API_SPEC.md` — `/pottery-inspection` 요청/응답 구조 상세
- `docs/ERD_INSPECTION_RESULT_POTTERY.md` — DB 저장 설계(아직 구현 전,
  참고용)
