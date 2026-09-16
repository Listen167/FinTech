from fastapi import APIRouter
from pydantic import BaseModel, Field


router = APIRouter()


class ChatRequest(BaseModel):
    session_id: str = Field(..., examples=["sess_001"])
    message: str = Field(..., examples=["Transfer 200 to Xiao Wang"])


class ChatResponse(BaseModel):
    session_id: str
    type: str
    message: str


@router.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    return ChatResponse(
        session_id=request.session_id,
        type="TEXT",
        message="Agent route scaffold is ready.",
    )

