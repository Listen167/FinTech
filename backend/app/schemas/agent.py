from pydantic import BaseModel, Field


class CreateAgentRunRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=2000)


class ConfirmationDecisionRequest(BaseModel):
    confirmed: bool


class VerificationCodeRequest(BaseModel):
    code: str = Field(pattern=r"^\d{6}$")


class AbortRunRequest(BaseModel):
    reason: str = Field(default="用户主动中断", max_length=200)


class HumanHandoffRequest(BaseModel):
    reason: str = Field(default="用户请求人工处理", min_length=2, max_length=200)
