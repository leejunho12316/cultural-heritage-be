You are Claude acting as an independent senior engineer reviewer. Please review the proposed improvement strategy for a local VCA shared-folder integration across three repos.

Context:
- Backend repo: /Users/csc9211/Documents/project/cultural-heritage-be
- Frontend repo: /Users/csc9211/Documents/project/cultural-heritage-fe
- VCA engine repo: /Users/csc9211/Documents/project/vca_v2
- Goal already implemented: FE calls only Spring /api/vca; Spring stores uploaded multipart bytes into a shared folder; Spring passes assessmentId, projectName, inputImageFolder to vca-ai; vca-ai validates the shared folder and runs `uv run python -m modules.orchestration.startup <projectName> <inputImageFolder> --dry-run`.
- Constraint: FE must not call vca-ai, vca_v2, filesystem, RDS, or S3 directly.
- Constraint: do not modify vca_v2 engine code if avoidable; use docker/env wiring where possible.
- Review gate: findings are advisory only; no file edits are authorized by this prompt.

Evidence from current files:
1. docker-compose.yml:
   - vca-ai env:
     - VCA_SHARED_STORAGE_ROOT=/shared/vca
     - VCA_ENGINE_ROOT=/vca_v2
     - VCA_DRY_RUN_TIMEOUT_SECONDS=120
   - vca-ai ports: "8002:8000"
   - vca-ai volumes:
     - ./shared:/shared
     - ../vca_v2:/vca_v2:ro
   - Spring env:
     - VCA_AI_BASE_URL=http://vca-ai:8000
     - VCA_AI_TIMEOUT_SECONDS=30
     - VCA_SHARED_STORAGE_ROOT=/shared/vca
     - VCA_SHARED_STORAGE_CONTAINER_ROOT=/shared/vca
     - no VCA_ACCESS_TOKEN is set
   - Spring ports: "8080:8080"

2. Spring application.yaml:
   - vca.access-token=${VCA_ACCESS_TOKEN:}
   - vca.ai.timeout-seconds=${VCA_AI_TIMEOUT_SECONDS:30}
   - vca.storage.local-root=${VCA_SHARED_STORAGE_ROOT:./shared/vca}
   - vca.storage.container-root=${VCA_SHARED_STORAGE_CONTAINER_ROOT:${VCA_SHARED_STORAGE_ROOT:./shared/vca}}

3. Spring VcaAccessTokenInterceptor:
   - if accessToken is empty OR method is OPTIONS, it allows the request.
   - if configured, it accepts X-VCA-Access-Token for all VCA routes.
   - for GET media paths only, it also accepts query parameter vca_access_token.

4. vca-ai assessment_runs.py:
   - validates inputImageFolder under VCA_SHARED_STORAGE_ROOT.
   - requires folder exists and contains supported image suffixes.
   - runs subprocess.run(["uv", "run", "python", "-m", "modules.orchestration.startup", projectName, inputDirectory, "--dry-run"], cwd=VCA_ENGINE_ROOT, check=True, capture_output=True, text=True, timeout=VCA_DRY_RUN_TIMEOUT_SECONDS).

5. vca_v2 startup.py:
   - default workspace_root is cwd.
   - derives output_root as workspace_root / "output" / "result" / project_name.
   - creates output_root and writes receipts under output/... during dry-run.

6. Spring VcaSharedStorage:
   - stores multipart upload bytes under storageRoot/uploads/{imageId}/{safeOriginalFilename}.
   - materializes run input under storageRoot/{assessmentRunId}/input/{imageId}-{safeOriginalFilename}.
   - validates MIME content type only.
   - no physical delete/cleanup methods currently.

Prior automated/local validation:
- Focused Spring VCA controller test passed with `bash ./gradlew test --tests com.aivle.conservation_backend.vca.VcaControllerTest`.
- vca-ai pytest passed: 7 tests.
- FE lint/build and mock build passed.
- docker compose config succeeded with missing env warnings.
- vca_v2 startup-focused pytest passed.
- Full Spring suite can pass contextLoads only when H2/AWS env overrides are provided; default local full suite fails because DB/AWS env are not configured.

Independent review findings so far:
- Goal reviewer FAIL: compose local integration likely fails because /vca_v2 is read-only but dry-run writes output/receipts under /vca_v2/output. Suggested writable output and uv cache/project env.
- QA executor PASS with residual risk: no live compose HTTP E2E was run; focused tests/builds pass.
- Security reviewer FAIL: VCA Spring API fail-open when VCA_ACCESS_TOKEN is unset; compose exposes backend 8080 and vca-ai 8002. Unauthenticated upload/run can write disk and trigger dry-run subprocess. Also vca-ai internal port is published without auth; MIME-only upload validation is weaker than image signature validation.
- Code quality reviewer FAIL: read-only /vca_v2 + uv run is a runtime risk; Spring timeout 30s is shorter than vca-ai dry-run 120s; uploaded files/run inputs are not cleaned up; tests mock subprocess and don't catch compose failure.

Question:
Please propose a prioritized improvement plan. Separate:
1. Minimal blocking fixes that should be applied before declaring this integration ready.
2. Security hardening that is important but can be staged if needed.
3. Medium-term architectural improvements.
For each recommendation, state the exact target files/configs, expected behavior, risks/tradeoffs, and tests/verification that should be added.

Be concrete and opinionated. Prefer minimal changes that preserve the current FE->Spring->vca-ai->vca_v2 dry-run architecture. Avoid speculative rewrites unless clearly justified.
