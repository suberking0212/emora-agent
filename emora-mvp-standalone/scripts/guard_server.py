"""Local OpenAI-compatible transport for official Qwen3Guard-Gen inference.

No classification rules or synthetic labels: content is returned from the model.
Run in .guard-venv; keep this inference runtime out of the Web environment.
"""
from contextlib import asynccontextmanager
import os
from pathlib import Path
import time
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field
from threading import Lock

ROOT = Path(__file__).resolve().parents[1]
MODEL_ID = os.environ.get("EMORA_QWEN_GUARD_MODEL", "Qwen/Qwen3Guard-Gen-0.6B")
MODEL_PATH = ROOT / ".guard-models" / MODEL_ID.rsplit("/", 1)[-1]
inference_lock = Lock()


@asynccontextmanager
async def lifespan(app):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    torch.set_num_threads(min(4, os.cpu_count() or 1))
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    app.state.tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH, local_files_only=True)
    app.state.model = AutoModelForCausalLM.from_pretrained(
        MODEL_PATH, local_files_only=True,
        torch_dtype=torch.float16 if device == "mps" else torch.float32,
    ).to(device).eval()
    print(f"Qwen3Guard ready: model={MODEL_ID} device={device}", flush=True)
    yield


app = FastAPI(lifespan=lifespan)


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str
    content: str = Field(min_length=1, max_length=50000)


class Completion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str
    messages: list[Message] = Field(min_length=1, max_length=2)
    stream: bool = False
    max_tokens: int = Field(default=128, ge=1, le=256)


def authorize(request):
    key = os.environ.get("EMORA_QWEN_GUARD_API_KEY", "EMPTY")
    if key != "EMPTY":
        import secrets
        if not secrets.compare_digest(request.headers.get("authorization", ""), f"Bearer {key}"):
            raise HTTPException(401, "Invalid API key")


@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL_ID}


@app.get("/v1/models")
def models(request: Request):
    authorize(request)
    return {"object": "list", "data": [{"id": MODEL_ID, "object": "model", "owned_by": "local"}]}


def generate(body):
    import torch

    with inference_lock, torch.inference_mode():
        tokenizer, model = app.state.tokenizer, app.state.model
        text = tokenizer.apply_chat_template(
            [message.model_dump() for message in body.messages], tokenize=False,
        )
        inputs = tokenizer([text], return_tensors="pt").to(model.device)
        prompt_tokens = inputs.input_ids.shape[1]
        if prompt_tokens + body.max_tokens > min(model.config.max_position_embeddings, 32768):
            raise HTTPException(400, "Context length exceeded")
        ids = model.generate(**inputs, max_new_tokens=body.max_tokens, do_sample=False)
        completion = ids[0][prompt_tokens:]
        content = tokenizer.decode(completion, skip_special_tokens=True)
        generated = len(completion)
        return {
            "id": f"chatcmpl-{uuid.uuid4().hex}", "object": "chat.completion",
            "created": int(time.time()), "model": MODEL_ID,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                         "finish_reason": "length" if generated == body.max_tokens else "stop"}],
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": generated,
                      "total_tokens": prompt_tokens + generated},
        }


@app.post("/v1/chat/completions")
async def completion(body: Completion, request: Request):
    authorize(request)
    if body.model != MODEL_ID:
        raise HTTPException(404, "Model not loaded")
    if body.stream or [m.role for m in body.messages] not in (["user"], ["user", "assistant"]):
        raise HTTPException(400, "Use non-streaming user or user+assistant moderation messages")
    return await run_in_threadpool(generate, body)
