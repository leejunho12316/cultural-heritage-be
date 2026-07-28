from pydantic import BaseModel, Field


class StitchJobRequest(BaseModel):
    jobId: str = Field(min_length=36, max_length=36)
    artifactId: str = Field(pattern=r"^artifact_\d{3}$")
    colorDirectory: str = Field(min_length=1)
    xrayDirectory: str = Field(min_length=1)
    outputDirectory: str = Field(min_length=1)
    configName: str = Field(min_length=1, max_length=200)
