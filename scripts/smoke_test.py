#!/usr/bin/env python3
"""Auth + messaging smoke test."""

from __future__ import annotations

import asyncio
import sys

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"


async def main() -> None:
    async with httpx.AsyncClient(base_url=BASE, timeout=15.0) as client:
        health = await client.get("/health")
        print("HEALTH:", health.json())

        login = await client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "admin123"},
        )
        login.raise_for_status()
        admin = login.json()
        token = admin["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        print("ADMIN LOGIN:", admin["user"]["username"], admin["user"]["status"])

        # Create pending user
        username = "alice_demo"
        created = await client.post(
            "/api/auth/register",
            headers=headers,
            json={
                "username": username,
                "password": "alice123",
                "display_name": "Alice",
                "role": "user",
            },
        )
        if created.status_code == 409:
            users = await client.get("/api/auth/users", headers=headers)
            users.raise_for_status()
            user = next(u for u in users.json()["users"] if u["username"] == username)
            print("USER EXISTS:", user["id"], user["status"])
        else:
            created.raise_for_status()
            user = created.json()
            print("CREATED:", user["username"], user["status"])

        # Pending login should fail
        pending_login = await client.post(
            "/api/auth/login",
            json={"username": username, "password": "alice123"},
        )
        print("PENDING LOGIN STATUS:", pending_login.status_code, pending_login.json())

        # Approve
        approved = await client.post(
            f"/api/auth/users/{user['id']}/approve",
            headers=headers,
        )
        approved.raise_for_status()
        print("APPROVED:", approved.json()["status"])

        user_login = await client.post(
            "/api/auth/login",
            json={"username": username, "password": "alice123"},
        )
        user_login.raise_for_status()
        user_token = user_login.json()["access_token"]
        user_headers = {"Authorization": f"Bearer {user_token}"}
        print("USER LOGIN OK")

        msg = await client.post(
            "/api/messages",
            headers=user_headers,
            json={"room_id": "general", "content": "Hello after approval!"},
        )
        msg.raise_for_status()
        print("MESSAGE:", msg.json()["content"])

        history = await client.get("/api/messages/general", headers=user_headers)
        history.raise_for_status()
        print("HISTORY total:", history.json()["total"])


if __name__ == "__main__":
    asyncio.run(main())
