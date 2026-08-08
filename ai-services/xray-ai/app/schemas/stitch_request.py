from __future__ import annotations

from pydantic import BaseModel, Field, HttpUrl, model_validator


class RemoteInput(BaseModel):
    fileName: str = Field(min_length=1, max_length=500)
    downloadUrl: HttpUrl


class StitchOutputPutUrls(BaseModel):
    assembled: HttpUrl
    layout: HttpUrl
    report: HttpUrl | None = None
    layoutFragmentMasks: HttpUrl


class StitchJobRequest(BaseModel):
    jobId: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    artifactId: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    configName: str = Field(min_length=1, max_length=200)
    colorInput: RemoteInput
    xrayInputs: list[RemoteInput] = Field(min_length=2, max_length=300)
    outputPutUrls: StitchOutputPutUrls
    callbackUrl: HttpUrl
    callbackToken: str | None = None

    @model_validator(mode="after")
    def validate_unique_names(self):
        names = [item.fileName for item in self.xrayInputs]
        if len(names) != len(set(names)):
            raise ValueError("xrayInputs fileName values must be unique")
        return self


class FinalizationOutputPutUrls(BaseModel):
    assembledFinal: HttpUrl
    sourceOwner: HttpUrl
    fragmentOwner: HttpUrl
    seamZone: HttpUrl
    overlapMask: HttpUrl
    provenance: HttpUrl


class FinalizationJobRequest(BaseModel):
    jobId: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    artifactId: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    xrayInputs: list[RemoteInput] = Field(min_length=2, max_length=300)
    layoutFragmentMasksDownloadUrl: HttpUrl
    finalLayoutDownloadUrl: HttpUrl
    outputPutUrls: FinalizationOutputPutUrls
    callbackUrl: HttpUrl
    callbackToken: str | None = None

    @model_validator(mode="after")
    def validate_unique_names(self):
        names = [item.fileName for item in self.xrayInputs]
        if len(names) != len(set(names)):
            raise ValueError("xrayInputs fileName values must be unique")
        return self


class LocalStitchJobRequest(BaseModel):
    """Internal path contract used only inside one FastAPI process."""

    jobId: str
    artifactId: str
    colorDirectory: str
    xrayDirectory: str
    outputDirectory: str
    configName: str
