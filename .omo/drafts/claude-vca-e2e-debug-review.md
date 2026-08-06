Please review this VCA real E2E debugging evidence and give an independent root-cause assessment plus next-step recommendation. Do not propose frontend-direct integration; the frontend must call Spring only.

Context:
- Backend repo: `/Users/csc9211/Documents/project/cultural-heritage-be`
- VCA engine repo: `/Users/csc9211/Documents/project/vca_v2`
- Goal: keep Dockerized Spring backend, run `vca-ai`/`vca_v2` locally, and verify VCA real E2E through Spring.
- Spring endpoint used: `POST /api/vca/{artifactId}/images`, then `POST /api/vca/{artifactId}/runs` with `X-VCA-Access-Token: local-vca-token`.

Docker-only real E2E result:
- Spring reached Docker `vca-ai`.
- Preprocessing succeeded with real JPEG: `device=cpu`, `detector_lane_status=real_executed`, `object_count=1`, `tile_count=306`.
- Startup progressed to `visual_cue_generation`; Qwen checkpoint loaded.
- Direct Docker startup ended `STARTUP_EXIT_STATUS=137` and Docker inspect showed `OOMKilled=true`.
- Interpretation under review: Docker real E2E is blocked by memory pressure in/around Qwen visual cue generation, not by Spring wiring.

Local VCA setup:
- tmux session: `vca-local-ai`
- local `vca-ai` health: `curl http://127.0.0.1:8000/health` -> `{"status":"ok","service":"vca-ai","mode":"deterministic"}`
- Spring override now has:
  - `VCA_AI_BASE_URL=http://host.docker.internal:8000`
  - `VCA_SHARED_STORAGE_CONTAINER_ROOT=/Users/csc9211/Documents/project/vca_v2/output/input`
  - host bind: `../vca_v2/output/input:/shared/vca/input-store`
  - output bind: `../vca_v2/output:/shared/vca/engine-output`
- local `vca-ai` env now has:
  - `VCA_SHARED_STORAGE_ROOT=/Users/csc9211/Documents/project/vca_v2/output/input`
  - `VCA_ENGINE_ROOT=/Users/csc9211/Documents/project/vca_v2`
  - `VCA_RUN_MODE=real`
  - `VCA_DEVICE=auto`
  - `VCA_MAX_IMAGES=1`
  - `VCA_MODEL_CACHE_ROOT=/Users/csc9211/Documents/project/vca_v2/models`
  - `VCA_SKIP_VISUAL_CUES=false`
  - `PYTORCH_ENABLE_MPS_FALLBACK=1`

Earlier local failure:
- `startup.json` failed at preprocessing with `outside workspace` because Spring materialized input under `/Users/csc9211/Documents/project/cultural-heritage-be/shared/vca/input-store`, outside `/Users/csc9211/Documents/project/vca_v2`.
- This was fixed by moving Spring's container root and host bind target to `/Users/csc9211/Documents/project/vca_v2/output/input`.

Current local real E2E after path fix:
- Artifact: `local-vca-20260806074020`
- Uploaded image ID: `1597c58e-bce3-44ac-9dee-7b57c0fc581a`
- Uploaded SHA256: `5388863ab0edd4c80ccad4bec8f15eb28c282cd5d61d92630392558df96d7b36`
- Host file exists at `/Users/csc9211/Documents/project/vca_v2/output/input/uploads/1597c58e-bce3-44ac-9dee-7b57c0fc581a/before01.jpg` with same SHA256.
- `POST /api/vca/local-vca-20260806074020/runs` still returns `502 VCA_AI_REQUEST_FAILED`.
- New startup receipt path: `/Users/csc9211/Documents/project/vca_v2/output/result/local-vca-20260806074020-92ddcbe0-09bb-4f60-986b-a991e4e522af/receipts/startup.json`
- Receipt shows:
  - `preprocessing` completed, `exit_code=0`
  - `rough_masking` failed, `exit_code=2`
  - later stages skipped because rough_masking failed
- `input_manifest.json` original path is inside workspace: `/Users/csc9211/Documents/project/vca_v2/output/input/92ddcbe0-09bb-4f60-986b-a991e4e522af/input/1597c58e-bce3-44ac-9dee-7b57c0fc581a-before01.jpg`
- `real_preprocessing_manifest.json` shows: `device=mps`, `detector_lane_status=real_executed`, `processed_image_count=1`, `object_count=1`, `tile_count=306`.

Direct rough_masking diagnosis:
- Stubbed `run_rough_masking_stage` with fake runner/adapter was able to load manifest and build adapter request, then returned 2 only because fake accepted candidates were zero. This confirms preprocessing manifest/path contract is valid.
- Direct real runner construction with hash verification raised:
  - `ContractValidationError invalid contract field 'model_inventory.models.owlv2_sam2.detector.revision': local cache content hash mismatch`
- Actual local cache hashes observed:
  - `owlv2_sam2.detector` inventory/expected: `sha256:487d13a62a7abb36183d35fb913effc45cd7288da075ae5f8bcf9b654034269b`
  - `owlv2_sam2.detector` actual: `sha256:98dee25b99f1d9376545a662879ab67577c65b84cc120bb027168bd21ce06fc7`
  - `sam2.segmenter` inventory/expected: `sha256:9a21bd21ee1a40eee76fe2eb7405482934607e3a4b6ea69048db76db3df0080f`
  - `sam2.segmenter` actual: `sha256:7388e98f1f1747de8789949d844635d9e75d8d2c6de7ff35b93711e1f9aa7ba9`
- Direct runner construction with `LocalModelCachePolicy(verify_hashes=False)` succeeded and produced `LocalModelRunner owlv2_sam2 ...`.

Question:
1. Are the current interpretations correct?
2. What should be the next least-invasive step to continue E2E validation?
3. Should we prefer regenerating/updating inventory hashes, redownloading exact model snapshots, adding a local-only bypass, or a code/config change that makes this explicit?
4. What risks should be called out before touching source-controlled inventory or local cache?
