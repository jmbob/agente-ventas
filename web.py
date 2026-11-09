#!/usr/bin/env python3
"""Agente de ventas con interfaz web (FastAPI + streaming SSE)."""

import argparse
import asyncio
from contextlib import asynccontextmanager
import json
import os
import uuid

from copilot.session_events import SessionEventType
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import uvicorn

from agente_core import ROOT, DEFAULT_MODEL, check_database, create_client, create_session, get_api_key


STATIC_DIR = ROOT / "static"
MODEL = os.environ.get("AGENTE_MODELO", DEFAULT_MODEL)
TURN_TIMEOUT = 300  # segundos máximos por respuesta


class Conversation:
    """Una sesión del agente por pestaña del navegador."""

    def __init__(self, session):
        self.session = session
        self.lock = asyncio.Lock()
        self.queue: asyncio.Queue | None = None
        self.loop = asyncio.get_running_loop()
        session.on(self._on_event)

    def _emit(self, item: dict) -> None:
        if self.queue is not None:
            self.loop.call_soon_threadsafe(self.queue.put_nowait, item)

    def _on_event(self, event) -> None:
        if event.type == SessionEventType.ASSISTANT_MESSAGE_DELTA:
            if event.data.delta_content:
                self._emit({"type": "delta", "text": event.data.delta_content})
        elif event.type == SessionEventType.TOOL_EXECUTION_START:
            args = event.data.arguments
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {"sql": args}
            sql = args.get("sql") if isinstance(args, dict) else None
            self._emit({"type": "tool", "sql": sql or str(args)})
        elif event.type == SessionEventType.SESSION_ERROR:
            self._emit({"type": "error", "message": event.data.message})


state: dict = {"client": None, "conversations": {}}


@asynccontextmanager
async def lifespan(app: FastAPI):
    check_database()
    get_api_key()  # falla pronto si no hay clave
    client = create_client()
    await client.start()
    state["client"] = client
    try:
        yield
    finally:
        for conv in state["conversations"].values():
            try:
                await conv.session.disconnect()
            except Exception:
                pass
        await client.stop()


app = FastAPI(title="Agente de ventas", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class ChatRequest(BaseModel):
    conversation_id: str | None = None
    message: str = Field(min_length=1, max_length=4000)


class ResetRequest(BaseModel):
    conversation_id: str


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/info")
async def info():
    return {"model": MODEL}


async def get_conversation(conversation_id: str | None) -> tuple[str, Conversation]:
    conversations = state["conversations"]
    if conversation_id and conversation_id in conversations:
        return conversation_id, conversations[conversation_id]
    conversation_id = uuid.uuid4().hex
    session = await create_session(state["client"], MODEL)
    conversations[conversation_id] = Conversation(session)
    return conversation_id, conversations[conversation_id]


def sse(item: dict) -> str:
    return f"data: {json.dumps(item, ensure_ascii=False)}\n\n"


@app.post("/api/chat")
async def chat(req: ChatRequest):
    message = req.message.strip()
    if not message:
        raise HTTPException(400, "Mensaje vacío")
    try:
        conversation_id, conv = await get_conversation(req.conversation_id)
    except Exception as exc:
        raise HTTPException(500, f"No se pudo crear la sesión: {exc}")
    if conv.lock.locked():
        raise HTTPException(409, "El agente todavía está respondiendo a la pregunta anterior")

    async def stream():
        async with conv.lock:
            queue: asyncio.Queue = asyncio.Queue()
            conv.queue = queue
            yield sse({"type": "start", "conversation_id": conversation_id})

            async def run_turn():
                try:
                    result = await conv.session.send_and_wait(message, timeout=TURN_TIMEOUT)
                    if result is None:
                        queue.put_nowait({"type": "error", "message": "El agente terminó sin responder"})
                except Exception as exc:
                    queue.put_nowait({"type": "error", "message": str(exc)})
                finally:
                    queue.put_nowait({"type": "done"})

            task = asyncio.create_task(run_turn())
            try:
                while True:
                    item = await queue.get()
                    yield sse(item)
                    if item["type"] == "done":
                        break
            finally:
                conv.queue = None
                if not task.done():
                    # El navegador cortó la conexión: abortamos el turno en curso.
                    try:
                        await conv.session.abort()
                    except Exception:
                        pass
                    task.cancel()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/reset")
async def reset(req: ResetRequest):
    conv = state["conversations"].pop(req.conversation_id, None)
    if conv is not None:
        try:
            await conv.session.disconnect()
        except Exception:
            pass
    return {"ok": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Agente de ventas con interfaz web")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--modelo", default=MODEL, help="Modelo de OpenRouter")
    args = parser.parse_args()
    MODEL = args.modelo
    print(f"Agente de ventas en http://{args.host}:{args.port}  (modelo: {MODEL})")
    uvicorn.run(app, host=args.host, port=args.port)
