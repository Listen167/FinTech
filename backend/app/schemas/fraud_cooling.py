"""反诈冷静期的严格请求契约。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class FraudAppealRequest(StrictModel):
    reason: str = Field(min_length=5, max_length=500)


class FraudReleaseVerificationRequest(StrictModel):
    code: str = Field(pattern=r"^\d{6}$")

