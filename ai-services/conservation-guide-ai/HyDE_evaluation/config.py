import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent / "app"
sys.path.insert(0, str(APP_DIR))

EVAL_DIR = Path(__file__).resolve().parent
PDFS_DIR = EVAL_DIR / "eval_original_pdfs"
VECTOR_STORE_DIR = EVAL_DIR / "eval_vector_store"
RESULTS_DIR = EVAL_DIR / "results"
GOLDEN_SET_PATH = EVAL_DIR / "golden_set.json"
COLLECTION_NAME = "hyde_eval_docs"

EVAL_LLM_MODEL: str = "gpt-5.4-nano"
EVAL_JUDGE_MODEL: str = "gpt-5.4-nano"
EVAL_TEMPERATURE: float = 0.7
EVAL_EMBED_MODEL: str = "text-embedding-3-small"
CHUNK_SIZE: int = 800
CHUNK_OVERLAP: int = 100
DEFAULT_K: int = 5
DEFAULT_RETRIEVAL_K: int = 4

# 강화제별 허용 용매 — 용매 정확도 판정 시 exact match 대신 이 목록으로 판단
COMPATIBLE_SOLVENTS: dict[str, list[str]] = {
    "Paraloid B72": ["아세톤", "톨루엔", "자일렌", "에틸아세테이트", "이소프로판올", "에탄올", "MEK", "아밀아세테이트"],
    "HPC": ["에탄올", "물", "메탄올", "이소프로판올", "아세톤"],
    "폴리비닐부티랄": ["에탄올", "메탄올", "이소프로판올", "아세톤", "톨루엔"],
    "수용성 Emulsion": ["물"],
    "Paraloid NAD-10": ["나프타", "화이트스피릿"],
}
