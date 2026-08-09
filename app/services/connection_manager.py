import asyncio
import json
from collections import defaultdict
from typing import Any

from fastapi import WebSocket


class ConnectionManager:
    """Tracks local WebSocket connections for rooms and users."""

    def __init__(self) -> None:
        self._room_connections: dict[str, set[WebSocket]] = defaultdict(set)
        self._user_connections: dict[str, set[WebSocket]] = defaultdict(set)
        self._socket_meta: dict[WebSocket, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def connect(
        self,
        websocket: WebSocket,
        *,
        user_id: str,
        user_name: str,
    ) -> None:
        await websocket.accept()
        async with self._lock:
            self._user_connections[user_id].add(websocket)
            self._socket_meta[websocket] = {
                "user_id": user_id,
                "user_name": user_name,
                "rooms": set(),
            }

    async def disconnect(self, websocket: WebSocket) -> list[str]:
        """Remove socket and return rooms that became empty locally."""
        empty_rooms: list[str] = []
        async with self._lock:
            meta = self._socket_meta.pop(websocket, None)
            if meta is None:
                return empty_rooms

            user_id = meta["user_id"]
            self._user_connections[user_id].discard(websocket)
            if not self._user_connections[user_id]:
                del self._user_connections[user_id]

            for room_id in list(meta["rooms"]):
                self._room_connections[room_id].discard(websocket)
                if not self._room_connections[room_id]:
                    del self._room_connections[room_id]
                    empty_rooms.append(room_id)
        return empty_rooms

    async def join_room(self, websocket: WebSocket, room_id: str) -> bool:
        async with self._lock:
            meta = self._socket_meta.get(websocket)
            if meta is None:
                return False
            meta["rooms"].add(room_id)
            self._room_connections[room_id].add(websocket)
            return True

    async def leave_room(self, websocket: WebSocket, room_id: str) -> bool:
        """Return True if the room has no more local connections."""
        async with self._lock:
            meta = self._socket_meta.get(websocket)
            if meta is None:
                return False
            meta["rooms"].discard(room_id)
            self._room_connections[room_id].discard(websocket)
            if not self._room_connections[room_id]:
                del self._room_connections[room_id]
                return True
            return False

    def get_meta(self, websocket: WebSocket) -> dict[str, Any] | None:
        return self._socket_meta.get(websocket)

    def rooms_with_local_clients(self) -> set[str]:
        return set(self._room_connections.keys())

    def users_with_local_clients(self) -> set[str]:
        return set(self._user_connections.keys())

    def is_user_in_room(self, user_id: str, room_id: str) -> bool:
        sockets = self._user_connections.get(user_id, set())
        for ws in sockets:
            meta = self._socket_meta.get(ws)
            if meta and room_id in meta["rooms"]:
                return True
        return False

    async def send_json(self, websocket: WebSocket, payload: dict[str, Any]) -> None:
        await websocket.send_text(json.dumps(payload, default=str))

    async def broadcast_room(self, room_id: str, payload: dict[str, Any]) -> None:
        sockets = list(self._room_connections.get(room_id, set()))
        dead: list[WebSocket] = []
        text = json.dumps(payload, default=str)
        for ws in sockets:
            try:
                await ws.send_text(text)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.disconnect(ws)

    async def broadcast_user(self, user_id: str, payload: dict[str, Any]) -> None:
        sockets = list(self._user_connections.get(user_id, set()))
        dead: list[WebSocket] = []
        text = json.dumps(payload, default=str)
        for ws in sockets:
            try:
                await ws.send_text(text)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.disconnect(ws)

    async def broadcast_all(self, payload: dict[str, Any]) -> None:
        sockets = list(self._socket_meta.keys())
        dead: list[WebSocket] = []
        text = json.dumps(payload, default=str)
        for ws in sockets:
            try:
                await ws.send_text(text)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.disconnect(ws)


manager = ConnectionManager()
