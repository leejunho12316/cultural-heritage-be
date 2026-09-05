# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Spring Backend

```bash
# Build
./gradlew build

# Run tests
./gradlew test

# Run a single test class
./gradlew test --tests "com.aivle.conservation_backend.vca.service.VcaControllerTest"

# Run a single test method
./gradlew test --tests "com.aivle.conservation_backend.vca.service.VcaControllerTest.methodName"
```

### Local Full Stack (Docker)

```bash
# First-time setup
mkdir -p shared/vca/input-store shared/vca/engine-output shared/vca/document-corpus
cp .env.example .env  # fill in required values

# Start all services
docker compose up --build -d

# Check logs
docker compose logs --tail 20 conservation-backend
docker compose logs --tail 20 xray-ai

# Health check
curl http://localhost:8080/api/xray/health
```

### Required `.env` Variables

`POSTGRES_PASSWORD`, `JWT_SECRET`, and `VCA_ACCESS_TOKEN` must be set — the app fails to start without them. `OPENAI_API_KEY` is needed for AI text generation features. `AWS_*` vars are needed for S3 image storage (X-Ray and VCA).

## Architecture

### System Overview

The frontend calls **only** the Spring backend (port 8080). Spring routes to AI services internally via Docker networking:

| Service | Port (host) | Role |
|---|---|---|
| `conservation-backend` | 8080 | Spring gateway for all FE requests |
| `conservation-guide-ai` | 8000 | LangGraph conservation guide AI |
| `xray-ai` | 8001 | X-ray fragment stitching + YOLO defect detection |
| `pottery-inspection-ai` | 8003 | Pottery material inspection |
| `vca-ai` | 8004 | Visual condition assessment (`vca_v2` pipeline) |
| `postgres` | 5432 | Shared DB (app data + LangGraph checkpointer) |

### Spring Package Structure (`src/main/java/com/aivle/conservation_backend/`)

- **`artifact/`** — Cultural artifact CRUD (owner-scoped)
- **`user/`** — User auth (JWT, BCrypt), posts, notices; `Role.ADMIN` gates admin endpoints
- **`common/config/`** — Cross-cutting beans: `WebSecurityConfig` (JWT filter, route auth rules), `S3Config` (AWS SDK v2, optional MinIO), `RestClientConfig`, `WebConfig` (CORS), `HibernateJsonConfig`
- **`xray_api/`** — X-ray workflow: presigned S3 upload → async stitch job (callback from xray-ai) → defect detection; `XrayJob`/`XrayDefect` entities persisted to PostgreSQL
- **`vca/`** — VCA workflow: image upload (presigned S3 or direct), analysis run lifecycle (`AssessmentRun`), report generation (Apache PDFBox), pottery auto-trigger; `VcaArtifactEntity`, `AssessmentRun`, `AssessmentReport`, `InspectionResultPottery`, `ReportPdfJob` entities
- **`conservation_guide_ai/`** — Conservation guide AI client and `Task` entity
- **`pottery_inspection_ai/`** — Pottery AI client + scheduler (`PotteryInspectionJobScheduler`) for async polling
- **`report_ai/`** — Unified report generation (adapters per source: Artifact, VCA, Xray, Pottery, ConservationGuide)
- **`photo/`** — Generic photo S3 upload

### Key Design Patterns

**Repository + Store split:** Each aggregate has a JPA `*Repository` interface and a `*Store` class (application-level abstraction used by services). Services depend on `*Store`, not `*Repository` directly.

**AI gateway pattern (VCA):** `VcaAiGateway` interface + `RestClientVcaAiGateway` implementation isolates HTTP calls to `vca-ai`. Similarly, each AI module has a `*Client` or `*Gateway` class.

**Security:** Stateless JWT (`JwtAuthenticationFilter` added before `UsernamePasswordAuthenticationFilter`). `/api/vca/**` additionally requires `X-VCA-Access-Token` header (enforced by `VcaAccessTokenInterceptor` via `VcaWebConfig`), matching `vca.access-token` property.

**Schema management:** `spring.jpa.hibernate.ddl-auto: validate` — Hibernate validates against existing schema but does NOT create/alter tables. Schema changes require SQL migration scripts in `db/`.

**VCA image storage:** Two modes selected at runtime — local shared-volume (`VcaSharedStorage`) or S3 presigned URLs (`VcaS3ImageStorage`). EKS uses S3 mode; local docker-compose uses shared volume.

**VCA pottery auto-trigger:** When a VCA run first reaches `COMPLETED`, if the material string contains "도자"/"pottery"/"ceramic", pottery inspection fires automatically (no FE action needed). Manual retry: `POST .../pottery-inspection`.

### AI Service Code Notes

- `ai-services/xray-ai/` — Python 3.12, FastAPI, YOLO (`ultralytics`). **Do not modify engine code** (`scripts/`, `xray_assembler/`, `configs/`, `mappings/`) — request changes from the original maintainer.
- `ai-services/vca-ai/engine/` — Vendored `vca_v2` source. **Do not modify directly** — make changes upstream and re-vendor.
- `ai-services/conservation-guide-ai/` — LangGraph with PostgreSQL checkpointer.

### Database

PostgreSQL is the production DB. H2 is available for tests only (test classpath). The `db/` directory contains incremental SQL migration scripts (applied manually). There is no migration framework (Flyway/Liquibase).
