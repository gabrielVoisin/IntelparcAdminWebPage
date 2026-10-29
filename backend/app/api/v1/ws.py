"""WebSocket for live updates: wss://<host>/api/v1/ws?token=<access_token>

Messages: {"event": "bay.updated" | "payment_alarm.created" | "payment_alarm.updated"
          | "device_event" | "controller.acknowledged", "data": {...}}
The website and the Android app (OkHttp WebSocket) refresh the affected view.
"""

from __future__ import annotations

import asyncio

import jwt
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.security import decode_access_token
from app.services.realtime import broadcaster

router = APIRouter(tags=["live"])


@router.websocket("/ws")
async def live(websocket: WebSocket) -> None:
    token = websocket.query_params.get("token") or websocket.cookies.get("pk_access")
    try:
        decode_access_token(token or "")
    except jwt.InvalidTokenError:
        await websocket.close(code=4401)
        return
    await websocket.accept()
    queue = broadcaster.subscribe()
    try:
        while True:
            try:
                message = await asyncio.wait_for(queue.get(), timeout=25)
                await websocket.send_text(message)
            except TimeoutError:
                await websocket.send_text('{"event": "ping", "data": {}}')
    except WebSocketDisconnect:
        pass
    finally:
        broadcaster.unsubscribe(queue)
