"""Locked schema versions and Qwen configuration constants."""

from typing import Final

INPUT_MANIFEST_SCHEMA_VERSION: Final = "anomaly-input-manifest-v1"
DETECTOR_ADAPTER_SCHEMA_VERSION: Final = "raw-image-detector-adapter-v1"
FINDING_SCHEMA_VERSION: Final = "raw-image-finding-v1"
REPORT_SCHEMA_VERSION: Final = "raw-image-anomaly-report-v1"
FINAL_REPORT_SCHEMA_VERSION: Final = "raw-image-final-report-v1"
CLI_SCHEMA_VERSION: Final = "raw-image-anomaly-cli-v1"
REPORT_STATIC_VERIFICATION_SCHEMA_VERSION: Final = (
    "raw-image-anomaly-report-static-v1"
)
FINAL_REPORT_STATIC_VERIFICATION_SCHEMA_VERSION: Final = (
    "raw-image-final-report-static-v1"
)
RAG_CONCEPT_SCHEMA_VERSION: Final = "rag-visual-concept-v1"
RAG_CITATION_SCHEMA_VERSION: Final = "rag-export-citation-v1"
BUDGET_APPROVAL_REQUEST_SCHEMA_VERSION: Final = "rag-budget-approval-request-v1"
BUDGET_APPROVAL_SCHEMA_VERSION: Final = "rag-budget-approval-v1"
USER_FOLLOWUP_REQUEST_SCHEMA_VERSION: Final = "user-followup-request-v1"

QWEN_BACKEND_KIND: Final = "independent_raw_image"
QWEN_BACKEND_DEPENDENCY: Final = "independent_raw_image_qwen_backend"
CANDIDATE_METADATA_BACKEND_ID: Final = "independent-raw-image-qwen"
QWEN_MODEL_ID: Final = "Qwen/Qwen2.5-VL-3B-Instruct"
PROMPT_ID: Final = "raw-image-visual-observation"
PROMPT_VERSION: Final = "v1"
VOCABULARY_VERSION: Final = "qwen-controlled-vocabulary-v1"
OBSERVATION_POLICY_ID: Final = "visual-only"
OBSERVATION_POLICY_VERSION: Final = "v1"

PROTECTED_BASELINE_RELATIVE_PATH: Final = (
    "runs/local_mac_selected7_20260724T161043256175Z"
)
NORMALIZED_DECIMAL_PLACES: Final = 6
