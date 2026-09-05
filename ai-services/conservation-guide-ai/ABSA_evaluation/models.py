from typing import Literal
from pydantic import BaseModel


class BaselineResult(BaseModel):
    """ABSA 구조 없이 전반적인 변화만 holistic하게 평가하는 Baseline 결과"""
    overall_severity: Literal["mild", "moderate", "severe"]
    description: str
