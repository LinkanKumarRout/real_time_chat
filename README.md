# Real-Time Notification Service

Python backend demo matching the resume project:

> Real-time messaging and notifications using **FastAPI WebSockets** + **Redis pub/sub**, with **MongoDB** chat history and REST retrieval APIs.

## Rooms (admin manage)

| Method | Path | Who | Description |
|--------|------|-----|-------------|
| `GET` | `/api/rooms` | Auth | List rooms |
| `POST` | `/api/rooms` | Admin | Create room |
| `PATCH` | `/api/rooms/{id}` | Admin | Rename room |
| `DELETE` | `/api/rooms/{id}` | Admin | Delete room + messages |

Default room `general` is seeded and cannot be deleted. Room names are lowercase `a-z`, `0-9`, `_`, `-`.

## Auth flow (admin create + approve)

Only an **admin** can create users. New users start as **pending** and cannot login/chat until approved.

1. Login as admin (`admin` / `admin123` by default)
2. Create a user from the sidebar admin panel (or `POST /api/auth/register`)
3. Click **Approve**
4. That user can now login and join rooms

| Method | Path | Who | Description |
|--------|------|-----|-------------|
| `POST` | `/api/auth/login` | Public | Login (approved only) |
| `GET` | `/api/auth/me` | Auth | Current user |
| `POST` | `/api/auth/register` | Admin | Create user (pending) |
| `GET` | `/api/auth/users` | Admin | List users |
| `POST` | `/api/auth/users/{id}/approve` | Admin | Approve user |
| `POST` | `/api/auth/users/{id}/reject` | Admin | Reject user |
| `DELETE` | `/api/auth/users/{id}` | Admin | Delete user (not self / not admins) |
| `DELETE` | `/api/auth/users/{id}` | Admin | Delete user (not self / not admins) |

WebSocket now requires a JWT:

```
ws://127.0.0.1:8000/ws?token=<access_token>
```

## Features

- Admin-only user registration + approval gate
- JWT auth for REST and WebSocket
- WebSocket chat rooms (`join` / `leave` / `send_message`)
- Redis pub/sub fan-out across workers
- MongoDB persistence for users, messages, notifications
- Messaging-style UI (chat bubbles, not raw JSON)
- OpenAPI docs at `/docs`

## Project layout

```
realtime_notification_service/
├── app/
│   ├── main.py                 # FastAPI app + lifespan
│   ├── config.py               # Settings from .env
│   ├── database.py             # Motor (async MongoDB)
│   ├── routers/
│   │   ├── api.py              # REST endpoints
│   │   └── ws.py               # WebSocket endpoint
│   ├── services/
│   │   ├── chat.py             # Message/notification persistence
│   │   ├── connection_manager.py
│   │   └── redis_pubsub.py
│   ├── schemas/                # Pydantic models
│   └── static/index.html       # Demo client
├── scripts/smoke_test.py
├── docker-compose.yml          # MongoDB + Redis
├── requirements.txt
└── .env.example
```

## Quick start

### 1. Start MongoDB + Redis

```bash
cd realtime_notification_service
docker compose up -d
```

### 2. Create venv and install deps

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

### 3. Run the API

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Open:

- Demo UI: http://127.0.0.1:8000/
- Swagger: http://127.0.0.1:8000/docs
- Health: http://127.0.0.1:8000/health

### 4. Smoke test (optional)

```bash
python scripts/smoke_test.py
```

## WebSocket protocol

Connect:

```
ws://127.0.0.1:8000/ws?user_id=alice&user_name=Alice
```

Client → server:

```json
{"action": "join", "room_id": "general"}
{"action": "send_message", "room_id": "general", "content": "Hello"}
{"action": "leave", "room_id": "general"}
{"action": "ping"}
```

Server → client events: `connected`, `joined`, `left`, `message`, `notification`, `user_joined`, `user_left`, `pong`, `error`.

## REST API

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/messages` | Create message + broadcast |
| `GET` | `/api/messages/{room_id}` | Chat history |
| `GET` | `/api/messages/detail/{message_id}` | Single message |
| `POST` | `/api/notifications` | Create + push notification |
| `GET` | `/api/notifications/{user_id}` | List notifications |
| `PATCH` | `/api/notifications/{user_id}/{id}/read` | Mark one read |
| `POST` | `/api/notifications/{user_id}/read-all` | Mark all read |

### Example: create message via REST

```bash
curl -X POST http://127.0.0.1:8000/api/messages \
  -H 'Content-Type: application/json' \
  -d '{
    "room_id": "general",
    "sender_id": "bob",
    "sender_name": "Bob",
    "content": "Hi from REST"
  }'
```

### Example: load history

```bash
curl 'http://127.0.0.1:8000/api/messages/general?limit=20'
```

## How it works

1. Client connects over WebSocket and joins a room.
2. On `send_message`, the server saves the message to MongoDB.
3. The server publishes the event to Redis channel `chat:room:{room_id}`.
4. Every app instance subscribed to that channel broadcasts to its local WebSocket clients.
5. Personal notifications use `chat:user:{user_id}` the same way.
6. REST history endpoints read from MongoDB independently of live sockets.

## Interview talking points

- Why Redis pub/sub instead of only in-memory broadcast (horizontal scale)
- MongoDB document model for chat history + indexes on `(room_id, created_at)`
- FastAPI async stack: Motor + redis-py asyncio + WebSockets
- Separation of REST (durable history) vs WebSocket (live delivery)
