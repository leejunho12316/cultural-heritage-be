from __future__ import annotations

from pydantic import BaseModel, Field, HttpUrl


class UrlDetectionFile(BaseModel):
    fileName: str = Field(min_length=1, max_length=500)
    downloadUrl: HttpUrl
    analysisTarget: str
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    sourceIndex: int | None = Field(default=None, ge=0)


class UrlDetectionBatch(BaseModel):
    files: list[UrlDetectionFile] = Field(min_length=1, max_length=300)
    analysisTarget: str
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
