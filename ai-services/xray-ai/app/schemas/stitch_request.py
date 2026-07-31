from pydantic import BaseModel, Field


class StitchJobRequest(BaseModel):
    jobId: str = Field(min_length=36, max_length=36)
    artifactId: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
    colorDirectory: str = Field(min_length=1)
    xrayDirectory: str = Field(min_length=1)
    outputDirectory: str = Field(min_length=1)
    configName: str = Field(min_length=1, max_length=200)
