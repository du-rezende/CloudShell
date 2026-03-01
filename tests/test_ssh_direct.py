"""
tests/test_ssh_direct.py — direct-call coverage tests for backend/services/ssh.py.

Covers all lines missed by the existing test suite:

- Line 152:    private_key_path branch in create_session
- Lines 189-190: malformed JSON in the initial resize frame (JSONDecodeError)
- Lines 214-229: ws_to_ssh body — raw/text frame dispatch, None-frame break,
                  resize control frame, invalid JSON control frame, plain stdin write
- Lines 245-246: ssh_to_ws exception handler
- Line 257:    pending task cancellation after asyncio.wait
- Lines 264-265: websocket.close() exception swallowing at end of stream_session
"""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import asyncssh
import pytest

from backend.services.ssh import (
    _Session,
    _sessions,
    create_session,
    stream_session,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_process(stdout_chunks: list) -> MagicMock:
    """Return a mock SSH process whose stdout.read() returns successive items."""
    process = MagicMock()
    process.stdin = MagicMock()
    process.stdin.write = MagicMock()
    process.change_terminal_size = MagicMock()
    process.stdout = MagicMock()
    chunks = list(stdout_chunks)

    async def _read(_n):
        if chunks:
            return chunks.pop(0)
        raise asyncio.CancelledError

    process.stdout.read = _read
    return process


def _inject(sid: str, process=None) -> MagicMock:
    """Insert a fake session and return its mock connection."""
    conn = MagicMock()
    conn.close = MagicMock()
    conn.wait_closed = AsyncMock()
    if process is not None:
        conn.create_process = AsyncMock(return_value=process)
    _sessions[sid] = _Session(conn=conn)
    return conn


def _make_ws(*recv_messages) -> MagicMock:
    """Build a WebSocket mock that yields the given receive() messages then cancels."""
    ws = MagicMock()
    ws.send_bytes = AsyncMock()
    ws.close = AsyncMock()
    queue = list(recv_messages)

    async def _recv():
        if queue:
            return queue.pop(0)
        raise asyncio.CancelledError

    ws.receive = _recv
    return ws


def _resize_msg(cols: int = 80, rows: int = 24) -> dict:
    return {"bytes": json.dumps({"type": "resize", "cols": cols, "rows": rows}).encode()}


# ── create_session: private_key_path branch (line 152) ────────────────────────

async def test_create_session_with_private_key():
    """create_session must add client_keys to connect_kwargs when private_key_path is set."""
    conn = MagicMock()
    conn.close = MagicMock()
    conn.wait_closed = AsyncMock()

    with patch("backend.services.ssh._known_hosts_path", return_value=None), \
         patch("asyncssh.connect", new_callable=AsyncMock, return_value=conn) as mock_connect:
        sid = await create_session(
            hostname="10.0.0.1",
            port=22,
            username="deploy",
            private_key_path="/tmp/id_rsa",
            known_hosts=None,
        )

    try:
        _, kwargs = mock_connect.call_args
        assert kwargs.get("client_keys") == ["/tmp/id_rsa"]
    finally:
        _sessions.pop(sid, None)


# ── initial resize: malformed JSON (lines 189-190) ────────────────────────────

async def test_stream_session_initial_malformed_json_uses_defaults():
    """A non-JSON initial frame must be silently ignored; default PTY dimensions are used."""
    sid = "init-bad-json"
    process = _make_process([b""])
    conn = _inject(sid, process)

    # Initial frame contains invalid JSON — hits JSONDecodeError branch
    ws = _make_ws({"bytes": b"not-json-at-all"})
    ws.send_bytes = AsyncMock()
    ws.close = AsyncMock()

    try:
        await stream_session(sid, ws)
    finally:
        _sessions.pop(sid, None)

    _, kwargs = conn.create_process.call_args
    # Malformed JSON → fallback dimensions kept
    assert kwargs["term_size"] == (220, 50)


async def test_stream_session_initial_non_resize_json_uses_defaults():
    """A valid JSON initial frame that is not a resize event must use default dimensions."""
    sid = "init-non-resize"
    process = _make_process([b""])
    conn = _inject(sid, process)

    ws = _make_ws({"bytes": json.dumps({"type": "ping"}).encode()})
    ws.send_bytes = AsyncMock()
    ws.close = AsyncMock()

    try:
        await stream_session(sid, ws)
    finally:
        _sessions.pop(sid, None)

    _, kwargs = conn.create_process.call_args
    assert kwargs["term_size"] == (220, 50)


# ── ws_to_ssh: stdin body (lines 214-229) ─────────────────────────────────────

async def test_stream_session_ws_to_ssh_plain_bytes():
    """Plain bytes from the WebSocket must be forwarded to SSH stdin."""
    sid = "ws-plain-bytes"
    process = _make_process([b""])
    conn = _inject(sid, process)

    # 1st msg = resize (PTY setup); 2nd msg = plain input bytes
    ws = _make_ws(_resize_msg(), {"bytes": b"ls -la\n"})
    ws.send_bytes = AsyncMock()
    ws.close = AsyncMock()

    try:
        await stream_session(sid, ws)
    finally:
        _sessions.pop(sid, None)

    process.stdin.write.assert_called_with(b"ls -la\n")


async def test_stream_session_ws_to_ssh_text_frame_encoded():
    """A text WebSocket frame must be encoded and forwarded to SSH stdin."""
    sid = "ws-text-frame"
    process = _make_process([b""])
    conn = _inject(sid, process)

    ws = _make_ws(_resize_msg(), {"text": "hello\n", "bytes": None})
    ws.send_bytes = AsyncMock()
    ws.close = AsyncMock()

    try:
        await stream_session(sid, ws)
    finally:
        _sessions.pop(sid, None)

    process.stdin.write.assert_called_with(b"hello\n")


async def test_stream_session_ws_to_ssh_none_frame_breaks():
    """A frame with neither bytes nor text must break the ws_to_ssh loop."""
    sid = "ws-none-frame"
    process = _make_process([b""])
    conn = _inject(sid, process)

    # First a resize (needed), then a None-payload frame → should break cleanly
    ws = _make_ws(_resize_msg(), {"bytes": None, "text": None})
    ws.send_bytes = AsyncMock()
    ws.close = AsyncMock()

    try:
        await stream_session(sid, ws)
    finally:
        _sessions.pop(sid, None)

    # The key assertion: no crash and stdin.write never called with None
    for call in process.stdin.write.call_args_list:
        assert call[0][0] is not None


async def test_stream_session_ws_to_ssh_resize_control_frame():
    """A JSON resize control frame during streaming must call change_terminal_size."""
    sid = "ws-ctrl-resize"
    process = _make_process([b""])
    conn = _inject(sid, process)

    resize_during = {"bytes": json.dumps({"type": "resize", "cols": 132, "rows": 50}).encode()}
    ws = _make_ws(_resize_msg(), resize_during)
    ws.send_bytes = AsyncMock()
    ws.close = AsyncMock()

    try:
        await stream_session(sid, ws)
    finally:
        _sessions.pop(sid, None)

    process.change_terminal_size.assert_called_with(132, 50)


async def test_stream_session_ws_to_ssh_bad_control_json_sent_as_stdin():
    """A frame starting with '{' that is invalid JSON must be sent to SSH stdin."""
    sid = "ws-bad-ctrl"
    process = _make_process([b""])
    conn = _inject(sid, process)

    bad_json = b"{not valid json"
    ws = _make_ws(_resize_msg(), {"bytes": bad_json})
    ws.send_bytes = AsyncMock()
    ws.close = AsyncMock()

    try:
        await stream_session(sid, ws)
    finally:
        _sessions.pop(sid, None)

    process.stdin.write.assert_called_with(bad_json)


async def test_stream_session_ws_to_ssh_non_resize_control_json_continues():
    """A valid JSON control frame with an unknown type must be skipped (continue), not sent to stdin."""
    sid = "ws-unknown-ctrl"
    process = _make_process([b""])
    conn = _inject(sid, process)

    # Valid JSON, starts with '{', but type is not 'resize' → continue (not stdin.write)
    unknown_ctrl = json.dumps({"type": "ping"}).encode()
    ws = _make_ws(_resize_msg(), {"bytes": unknown_ctrl})
    ws.send_bytes = AsyncMock()
    ws.close = AsyncMock()

    try:
        await stream_session(sid, ws)
    finally:
        _sessions.pop(sid, None)

    # stdin.write must NOT have been called with the ping frame
    for call in process.stdin.write.call_args_list:
        assert call[0][0] != unknown_ctrl


# ── ssh_to_ws: exception handler (lines 245-246) ──────────────────────────────

async def test_stream_session_ssh_to_ws_exception_swallowed():
    """An exception in ssh_to_ws must be swallowed so stream_session completes."""
    sid = "ssh-to-ws-exc"
    process = _make_process([])
    conn = _inject(sid, process)

    # Make stdout.read raise immediately — hits the except block
    process.stdout.read = AsyncMock(side_effect=RuntimeError("pipe broken"))

    ws = _make_ws(_resize_msg())
    ws.send_bytes = AsyncMock()
    ws.close = AsyncMock()

    # Must complete without raising
    try:
        await stream_session(sid, ws)
    finally:
        _sessions.pop(sid, None)


# ── pending task cancellation (line 257) ─────────────────────────────────────

async def test_stream_session_pending_tasks_are_cancelled():
    """When ssh_to_ws finishes first, the still-running ws_to_ssh task must be cancelled."""
    sid = "pending-cancel"

    # ssh_to_ws ends immediately (empty read); ws_to_ssh blocks forever on receive()
    process = _make_process([b""])  # empty chunk → ssh_to_ws loop exits
    conn = _inject(sid, process)

    # receive() never returns — ws_to_ssh stays pending until cancelled
    ws = MagicMock()
    ws.send_bytes = AsyncMock()
    ws.close = AsyncMock()

    async def _recv_block():
        await asyncio.sleep(9999)  # blocks until cancelled

    # First call is the initial resize wait_for (will timeout); subsequent calls block.
    # We want: initial wait_for to timeout → defaults used → then ws_to_ssh blocks.
    real_wait_for = asyncio.wait_for
    call_n = {"n": 0}

    async def _patched_wait_for(coro, timeout):
        call_n["n"] += 1
        if call_n["n"] == 1:
            coro.close()
            raise asyncio.TimeoutError
        return await real_wait_for(coro, timeout)

    ws.receive = AsyncMock(side_effect=_recv_block)

    with patch("asyncio.wait_for", side_effect=_patched_wait_for):
        try:
            await stream_session(sid, ws)
        finally:
            _sessions.pop(sid, None)

    # If we reach here, the pending ws_to_ssh task was cancelled without error
    ws.close.assert_called_with(code=1000)


# ── websocket.close() exception swallowing (lines 264-265) ───────────────────

async def test_stream_session_ws_close_exception_swallowed():
    """An exception from websocket.close() at stream end must be swallowed."""
    sid = "ws-close-exc"
    process = _make_process([b""])
    conn = _inject(sid, process)

    ws = _make_ws(_resize_msg())
    ws.send_bytes = AsyncMock()
    ws.close = AsyncMock(side_effect=RuntimeError("already closed"))

    # Must not raise
    try:
        await stream_session(sid, ws)
    finally:
        _sessions.pop(sid, None)
