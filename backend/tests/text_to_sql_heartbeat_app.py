"""Uvicorn target for the heartbeat regression. Pytest does not collect this module."""

from __future__ import annotations

import asyncio

from fastapi import FastAPI
from fastapi.responses import StreamingResponse

import education_platform.modules.text_to_sql.guards as guards_mod
import education_platform.modules.text_to_sql.nodes as nodes
import education_platform.modules.text_to_sql.stream as stream_mod
from education_platform.modules.text_to_sql.run_store import MemoryRunStore
from education_platform.modules.text_to_sql.state import TextToSQLState

stream_mod.HEARTBEAT_SECONDS = 0.25

_STORE = MemoryRunStore()
_USER = "00000000-0000-0000-0000-000000000001"
_REQUEST = "11111111-1111-4111-8111-111111111111"


async def _guards(state: TextToSQLState) -> TextToSQLState:
    await asyncio.sleep(0.7)
    return state


async def _pass_through(state: TextToSQLState) -> TextToSQLState:
    return state


async def _intent(state: TextToSQLState) -> TextToSQLState:
    return {**state, "intent_route": "free_form", "error": None}


async def _generate(state: TextToSQLState) -> TextToSQLState:
    return {**state, "generated_sql": "SELECT 1", "schema_context": "subjects(id)", "error": None}


async def _validate(state: TextToSQLState) -> TextToSQLState:
    return {**state, "validated_sql": "SELECT 1", "error": None}


async def _execute(state: TextToSQLState) -> TextToSQLState:
    return {
        **state,
        "query_result": [{"student_count": 1}],
        "result_row_count": 1,
        "error": None,
    }


async def _sanity(state: TextToSQLState) -> TextToSQLState:
    return {**state, "confidence": "high"}


async def _audit(state: TextToSQLState) -> TextToSQLState:
    return state


guards_mod.run_data_guards = _guards
for _name, _fn in (
    ("intent_router", _intent),
    ("load_schema", _generate),
    ("link_schema", _pass_through),
    ("generate_sql", _generate),
    ("validate_sql", _validate),
    ("apply_role_scope", _pass_through),
    ("execute_sql", _execute),
    ("sanity_check", _sanity),
    ("audit_log", _audit),
    ("compose_answer", _pass_through),
):
    setattr(nodes, _name, _fn)

app = FastAPI()


@app.post("/stream")
async def stream() -> StreamingResponse:
    state: TextToSQLState = {
        "question": "How many students do I teach?",
        "user_id": _USER,
        "user_role": "teacher",
        "institution_id": _USER,
        "schema_context": "",
        "retry_count": 0,
        "audit_entry": {},
        "stream_request_id": _REQUEST,
    }
    return StreamingResponse(
        stream_mod.iter_ask_stream(
            state,
            request_id=_REQUEST,
            timeout_seconds=10,
            timeout_message="timeout",
            unexpected_message="unexpected",
            heartbeat_seconds=0.25,
            store=_STORE,
        ),
        media_type="text/event-stream",
    )
