from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.llm_chat.model import Message, generate_reply
from app.shared_models import ModelMissingError

router = APIRouter(prefix="/llm-chat")


class ChatMessage(BaseModel):
    role: str  # "user" | "assistant"
    content: str


class ChatRequest(BaseModel):
    history: list[ChatMessage]


class ChatResponse(BaseModel):
    reply: str


@router.post("/messages", response_model=ChatResponse)
def send_message(body: ChatRequest):
    history: list[Message] = [{"role": m.role, "content": m.content} for m in body.history]
    try:
        reply = generate_reply(history)
    except ModelMissingError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return ChatResponse(reply=reply)
