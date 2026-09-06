import sys
from pathlib import Path

# app/schemas.py 등 임포트를 위해 app/ 디렉터리를 경로에 추가
APP_DIR = Path(__file__).resolve().parent.parent / "app"
sys.path.insert(0, str(APP_DIR))

# 평가용 Vision LM — 프로덕션(gpt-4o)보다 성능이 높은 모델 사용
EVAL_VISION_MODEL: str = "gpt-5.4-nano"

# 일관도 측정을 위해 temperature > 0 필수 (프로덕션 temperature=0과 분리)
EVAL_TEMPERATURE: float = 0.7

# 기본 반복 횟수
DEFAULT_K: int = 5
