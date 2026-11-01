"""Channel Gateway 的统一入站消息协议。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


ChannelName = Literal["APP", "FEISHU", "WECHAT_WORK", "DINGTALK"]


class StrictChannelModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CreateChannelLinkTokenRequest(StrictChannelModel):
    channel: ChannelName


class InboundChannelMessage(StrictChannelModel):
    schema_version: Literal["1.0"] = "1.0"
    channel: ChannelName
    tenant_id: str = Field(min_length=1, max_length=128)
    external_message_id: str = Field(min_length=1, max_length=255)
    external_conversation_id: str = Field(min_length=1, max_length=255)
    external_user_id: str = Field(min_length=1, max_length=255)
    kind: Literal["TEXT", "LINK"]
    text: str | None = Field(default=None, min_length=1, max_length=2000)
    link_code: str | None = Field(default=None, min_length=6, max_length=32)
    occurred_at: datetime

    @model_validator(mode="after")
    def validate_kind_payload(self):
        if self.kind == "TEXT" and not self.text:
            raise ValueError("TEXT 消息必须提供 text")
        if self.kind == "LINK" and not self.link_code:
            raise ValueError("LINK 消息必须提供 link_code")
        if self.kind == "TEXT" and self.link_code is not None:
            raise ValueError("TEXT 消息不能携带 link_code")
        if self.kind == "LINK" and self.text is not None:
            raise ValueError("LINK 消息不能携带 text")
        return self
