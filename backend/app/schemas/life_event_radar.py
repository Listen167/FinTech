"""生活事件雷达 API 请求结构。"""

from pydantic import BaseModel, ConfigDict, Field


class StrictRadarModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RadarSimulationRequest(StrictRadarModel):
    advance_days: int = Field(default=30, ge=1, le=365)
