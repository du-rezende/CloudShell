"""
tests/test_audit_direct.py — direct-call coverage tests for backend/routers/audit.py.

The ASGI transport used by httpx does NOT propagate Python's sys.settrace into
handler coroutines, so pytest-cov cannot record those lines even when the HTTP
tests pass.  Calling handlers directly keeps the coverage tracer active.

Covers all lines missed by the ASGI-based test suite:
- list_audit_logs: lines 65-87 (entire handler body)
- trigger_prune:   line 98    (return PruneResult)
"""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.models.audit import AuditLog
from backend.routers.audit import list_audit_logs, trigger_prune


# -- Fake helpers --------------------------------------------------------------

class _FakeDB:
    """Minimal AsyncSession duck-type for audit handler tests."""

    def __init__(self, total: int = 0, rows: list | None = None):
        self._total = total
        self._rows = rows or []
        self._call = 0

    async def execute(self, stmt):
        self._call += 1
        result = MagicMock()
        if self._call == 1:
            # First call: COUNT(*) query
            result.scalar_one.return_value = self._total
        else:
            # Second call: paginated rows query
            result.scalars.return_value.all.return_value = self._rows
        return result


def _make_row(
    id: int = 1,
    username: str = "admin",
    action: str = "login",
    source_ip: str | None = "1.2.3.4",
    detail: str | None = "User logged in",
) -> AuditLog:
    """Return a minimal AuditLog ORM instance for testing."""
    row = AuditLog(
        username=username,
        action=action,
        source_ip=source_ip,
        detail=detail,
        timestamp=datetime.now(timezone.utc),
    )
    row.id = id
    return row


# -- list_audit_logs -----------------------------------------------------------

async def test_list_audit_logs_empty():
    """list_audit_logs returns an empty AuditLogPage when there are no entries."""
    db = _FakeDB(total=0, rows=[])
    result = await list_audit_logs(page=1, page_size=50, _="admin", db=db)

    assert result.total == 0
    assert result.page == 1
    assert result.page_size == 50
    assert result.entries == []


async def test_list_audit_logs_returns_entries():
    """list_audit_logs maps AuditLog rows to AuditLogEntry objects correctly."""
    row = _make_row(id=7, username="bob", action="logout", source_ip="10.0.0.1", detail="Bye")
    db = _FakeDB(total=1, rows=[row])

    result = await list_audit_logs(page=1, page_size=50, _="admin", db=db)

    assert result.total == 1
    assert len(result.entries) == 1
    entry = result.entries[0]
    assert entry.id == 7
    assert entry.username == "bob"
    assert entry.action == "logout"
    assert entry.source_ip == "10.0.0.1"
    assert entry.detail == "Bye"
    # timestamp must be an ISO-8601 string
    assert "T" in entry.timestamp


async def test_list_audit_logs_pagination_params():
    """list_audit_logs honours page and page_size in the returned AuditLogPage."""
    rows = [_make_row(id=i) for i in range(1, 6)]
    db = _FakeDB(total=42, rows=rows)

    result = await list_audit_logs(page=3, page_size=5, _="admin", db=db)

    assert result.total == 42
    assert result.page == 3
    assert result.page_size == 5
    assert len(result.entries) == 5


async def test_list_audit_logs_null_source_ip_and_detail():
    """list_audit_logs handles rows with null source_ip and detail without error."""
    row = _make_row(id=99, source_ip=None, detail=None)
    db = _FakeDB(total=1, rows=[row])

    result = await list_audit_logs(page=1, page_size=50, _="admin", db=db)

    entry = result.entries[0]
    assert entry.source_ip is None
    assert entry.detail is None


# -- trigger_prune -------------------------------------------------------------

async def test_trigger_prune_returns_result():
    """trigger_prune calls prune_old_entries and returns a PruneResult."""
    db = _FakeDB()

    with patch("backend.routers.audit.prune_old_entries", new_callable=AsyncMock, return_value=5) as mock_prune, \
         patch("backend.routers.audit.get_settings") as mock_settings:
        mock_settings.return_value.audit_retention_days = 90

        result = await trigger_prune(_="admin", db=db)

    assert result.deleted == 5
    assert result.retention_days == 90
    mock_prune.assert_awaited_once_with(db, 90)


async def test_trigger_prune_zero_deleted():
    """trigger_prune returns deleted=0 when no entries are pruned."""
    db = _FakeDB()

    with patch("backend.routers.audit.prune_old_entries", new_callable=AsyncMock, return_value=0), \
         patch("backend.routers.audit.get_settings") as mock_settings:
        mock_settings.return_value.audit_retention_days = 30

        result = await trigger_prune(_="admin", db=db)

    assert result.deleted == 0
    assert result.retention_days == 30
