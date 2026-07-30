from pydantic import BaseModel


class StitchJobResponse(BaseModel):
    jobId: str
    artifactId: str
    status: str
    message: str


class StitchJobStatusResponse(BaseModel):
    jobId: str
    artifactId: str
    status: str
    message: str
    resultUrl: str | None = None
    errorMessage: str | None = None