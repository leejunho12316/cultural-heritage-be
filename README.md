# conservation_backend

문화재 보존처리 관리 시스템 — Spring 백엔드 + LangGraph 기반 AI 서비스(`ai-services/conservation-guide-ai`)로 구성.

## 기술 스택 버전

### Spring 백엔드 (`/src`)
| 항목 | 버전 |
|---|---|
| Java | 17 (`Dockerfile`: `eclipse-temurin:17-jdk` / `17-jre`) |
| Spring Boot | 4.1.0 |
| io.spring.dependency-management 플러그인 | 1.1.7 |
| Build tool | Gradle (`gradlew`) |
| DB (로컬/테스트) | H2 |

주요 의존성: `spring-boot-starter-data-jpa`, `spring-boot-starter-validation`, `spring-boot-starter-webmvc`, Lombok(compile-only), `spring-boot-devtools`(dev), JUnit Platform(test). 버전은 Spring Boot 4.1.0 BOM이 관리.

### conservation-guide-ai (`/ai-services/conservation-guide-ai`)
| 항목 | 버전 |
|---|---|
| Python (배포 기준, `Dockerfile`) | 3.12 (`python:3.12-slim`) |

`app/requirements.txt` 고정 버전:
| 패키지 | 버전 |
|---|---|
| langgraph | 1.2.9 |
| langgraph-checkpoint-postgres | 3.1.0 |
| psycopg[binary,pool] | 3.3.4 |
| langchain-openai | 1.3.5 |
| fastapi | 0.139.2 |
| uvicorn | 0.51.0 |
| pydantic | 2.13.4 |
| python-dotenv | 1.2.2 |

> 로컬 개발 venv가 3.12보다 최신 Python으로 구성되어 있다면, 배포 환경(Docker)과의 문법 호환성 문제가 없는지 유의할 것.

### 인프라 (`docker-compose.yml`)
| 항목 | 버전 |
|---|---|
| PostgreSQL | `postgres:16` |

`postgres`(LangGraph 체크포인터 + 앱 DB), `conservation-guide-ai`(FastAPI, 8000), `conservation-backend`(Spring, 8080) 3개 컨테이너로 구성.
