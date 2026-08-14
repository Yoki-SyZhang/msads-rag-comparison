"""FastAPI server for the MSADS RAG agent.

Start:
    uvicorn app:app --host 0.0.0.0 --port 8000 --reload

POST /chat
    Body: {"query": "...", "history": [{"role": "user", "content": "..."}, ...]}
    Returns: {"answer": "...", "citations": [...], "debug": {...}}
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from agent import agent_loop, answer_generator, logger
from agent.deepseek_client import DeepSeekClient
from agent.tools import AgentTools

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


# ---------------------------------------------------------------------------
# Shared singletons (loaded once at startup)
# ---------------------------------------------------------------------------

_tools: AgentTools
_client: DeepSeekClient


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _tools, _client
    project_root = Path(__file__).resolve().parent
    _tools = AgentTools(index_dir=str(project_root / "index"))
    _client = DeepSeekClient()
    print(f"[startup] DeepSeek API key configured: {_client.is_available()}", flush=True)
    # Pre-warm the index by accessing it
    _ = _tools.loaded_index
    print("[startup] Index loaded.", flush=True)
    yield


app = FastAPI(title="MSADS RAG Agent", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request / Response schemas (Pydantic, for FastAPI validation)
# ---------------------------------------------------------------------------

class HistoryMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    query: str
    history: List[HistoryMessage] = []


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "deepseek_configured": _client.is_available(),
        "model": _client.model,
    }


@app.post("/chat")
async def chat(request: ChatRequest) -> Dict[str, Any]:
    if not request.query.strip():
        raise HTTPException(status_code=400, detail="query must not be empty")

    history = [{"role": m.role, "content": m.content} for m in request.history]

    # Run agent loop
    memory = agent_loop.run(
        query=request.query,
        history=history,
        tools=_tools,
        client=_client,
    )

    # Generate answer
    response = answer_generator.generate(memory, _client)

    # Write log
    log_path = logger.write_log(memory, response)
    response.debug["log_path"] = str(log_path)

    return response.to_dict()
