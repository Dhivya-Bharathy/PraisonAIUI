"""Persist voice call state and webhook idempotency."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _state_db_path() -> str:
    env = os.getenv("PRAISONAI_VOICE_DIR")
    base = Path(env) if env else Path.home() / ".praisonai" / "voice"
    base.mkdir(parents=True, exist_ok=True)
    return str(base / "voice_state.db")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_state_db_path())
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS voice_calls (
            call_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            customer_number TEXT,
            transcript TEXT NOT NULL DEFAULT '',
            summary TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS voice_webhook_events (
            event_key TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            processed_at TEXT NOT NULL
        )
        """
    )
    return conn


def webhook_event_key(event_type: str, payload: dict[str, Any]) -> str:
    message = payload.get("message") if isinstance(payload.get("message"), dict) else payload
    call = (message or {}).get("call") if isinstance(message, dict) else {}
    call_id = call.get("id") if isinstance(call, dict) else None
    if event_type == "transcript" and isinstance(message, dict):
        text = str(message.get("transcript") or "")
        role = str(message.get("role") or "")
        kind = str(message.get("transcriptType") or "")
        return f"{event_type}:{call_id}:{role}:{kind}:{text}"
    if call_id:
        return f"{event_type}:{call_id}:{json.dumps(message, sort_keys=True)[:200]}"
    return f"{event_type}:{json.dumps(payload, sort_keys=True)[:300]}"


class VoiceCallStore:
    @staticmethod
    def mark_event_processed(event_key: str, event_type: str) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        with closing(_connect()) as conn, conn:
            row = conn.execute(
                "SELECT 1 FROM voice_webhook_events WHERE event_key = ?",
                (event_key,),
            ).fetchone()
            if row:
                return False
            conn.execute(
                """
                INSERT INTO voice_webhook_events (event_key, event_type, processed_at)
                VALUES (?, ?, ?)
                """,
                (event_key, event_type, now),
            )
        return True

    @staticmethod
    def upsert_call(
        call_id: str,
        *,
        status: str = "unknown",
        customer_number: str | None = None,
        transcript: str | None = None,
        summary: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with closing(_connect()) as conn, conn:
            row = conn.execute(
                "SELECT transcript, summary, metadata_json, customer_number FROM voice_calls WHERE call_id = ?",
                (call_id,),
            ).fetchone()
            existing_transcript = row[0] if row else ""
            existing_summary = row[1] if row else ""
            existing_meta = json.loads(row[2]) if row and row[2] else {}
            existing_number = row[3] if row else None
            merged_meta = {**existing_meta, **(metadata or {})}
            conn.execute(
                """
                INSERT INTO voice_calls (
                    call_id, status, customer_number, transcript, summary, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(call_id) DO UPDATE SET
                    status = excluded.status,
                    customer_number = COALESCE(excluded.customer_number, voice_calls.customer_number),
                    transcript = excluded.transcript,
                    summary = COALESCE(excluded.summary, voice_calls.summary),
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at
                """,
                (
                    call_id,
                    status,
                    customer_number or existing_number,
                    transcript if transcript is not None else existing_transcript,
                    summary if summary is not None else existing_summary,
                    json.dumps(merged_meta, sort_keys=True),
                    now,
                    now,
                ),
            )

    @staticmethod
    def append_transcript_line(call_id: str, line: str) -> str:
        with closing(_connect()) as conn:
            row = conn.execute(
                "SELECT transcript FROM voice_calls WHERE call_id = ?",
                (call_id,),
            ).fetchone()
        existing = row[0] if row else ""
        full = f"{existing}\n{line}".strip() if existing else line
        VoiceCallStore.upsert_call(call_id, transcript=full)
        return full

    @staticmethod
    def list_calls(limit: int = 50) -> list[dict[str, Any]]:
        with closing(_connect()) as conn:
            rows = conn.execute(
                """
                SELECT call_id, status, customer_number, transcript, summary, metadata_json, updated_at
                FROM voice_calls ORDER BY updated_at DESC LIMIT ?
                """,
                (max(1, limit),),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            out.append(
                {
                    "call_id": row[0],
                    "status": row[1],
                    "customer_number": row[2],
                    "transcript": row[3],
                    "summary": row[4],
                    "metadata": json.loads(row[5] or "{}"),
                    "updated_at": row[6],
                }
            )
        return out

    @staticmethod
    def get_call(call_id: str) -> dict[str, Any] | None:
        with closing(_connect()) as conn:
            row = conn.execute(
                """
                SELECT call_id, status, customer_number, transcript, summary, metadata_json, updated_at
                FROM voice_calls WHERE call_id = ?
                """,
                (call_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "call_id": row[0],
            "status": row[1],
            "customer_number": row[2],
            "transcript": row[3],
            "summary": row[4],
            "metadata": json.loads(row[5] or "{}"),
            "updated_at": row[6],
        }
