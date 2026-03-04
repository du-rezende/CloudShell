"""
tests/test_config_transfer.py — tests for GET /api/config/export and POST /api/config/import.

Tests cover:
- GET /api/config/export       requires auth; returns JSON with all devices (password + key);
                               decrypted passwords and keys are present in the export;
                               returns 200 with Content-Disposition attachment header
- POST /api/config/import      requires auth; imports new devices; skips duplicates;
                               rejects invalid JSON; rejects wrong format_version;
                               re-encrypts credentials with the target key;
                               handles password and key-auth devices correctly
"""
import json
import io

import pytest

from backend.services.crypto import decrypt, generate_key_pair, load_decrypted_key
from backend.config import get_settings


# ── Helpers ───────────────────────────────────────────────────────────────────

def _password_device(name: str = "srv", hostname: str = "10.0.0.1", port: int = 22) -> dict:
    return {
        "name": name,
        "hostname": hostname,
        "port": port,
        "username": "admin",
        "auth_type": "password",
        "connection_type": "ssh",
        "password": "s3cr3t",
    }


def _key_device(pem: str, name: str = "key-srv", hostname: str = "10.0.0.2", port: int = 22) -> dict:
    return {
        "name": name,
        "hostname": hostname,
        "port": port,
        "username": "deploy",
        "auth_type": "key",
        "connection_type": "ssh",
        "private_key": pem,
    }


# ── Export ────────────────────────────────────────────────────────────────────

async def test_export_requires_auth(client):
    """Unauthenticated GET /api/config/export must return 401."""
    resp = await client.get("/api/config/export")
    assert resp.status_code == 401


async def test_export_empty(auth_client):
    """Exporting with no devices yields an empty devices list."""
    resp = await auth_client.get("/api/config/export")
    assert resp.status_code == 200
    data = resp.json()
    assert data["format_version"] == 1
    assert data["device_count"] == 0
    assert data["devices"] == []


async def test_export_content_disposition(auth_client):
    """Export response includes a Content-Disposition attachment header."""
    resp = await auth_client.get("/api/config/export")
    assert resp.status_code == 200
    cd = resp.headers.get("content-disposition", "")
    assert "attachment" in cd
    assert "cloudshell-config.json" in cd


async def test_export_password_device(auth_client):
    """Exported password-auth device contains the plaintext password."""
    await auth_client.post("/api/devices/", json=_password_device())
    resp = await auth_client.get("/api/config/export")
    assert resp.status_code == 200
    data = resp.json()
    assert data["device_count"] == 1
    dev = data["devices"][0]
    assert dev["auth_type"] == "password"
    assert dev["password"] == "s3cr3t"
    assert dev["private_key"] is None


async def test_export_key_device(auth_client):
    """Exported key-auth device contains the plaintext PEM key."""
    pem, _ = generate_key_pair()
    # Use a small key to avoid slow generation in tests — reuse generated PEM
    await auth_client.post("/api/devices/", json=_key_device(pem))
    resp = await auth_client.get("/api/config/export")
    assert resp.status_code == 200
    data = resp.json()
    assert data["device_count"] == 1
    dev = data["devices"][0]
    assert dev["auth_type"] == "key"
    assert dev["password"] is None
    assert dev["private_key"] == pem


async def test_export_multiple_devices(auth_client):
    """All devices are included in the export."""
    pem, _ = generate_key_pair()
    await auth_client.post("/api/devices/", json=_password_device(name="a", hostname="1.1.1.1"))
    await auth_client.post("/api/devices/", json=_password_device(name="b", hostname="2.2.2.2"))
    await auth_client.post("/api/devices/", json=_key_device(pem, hostname="3.3.3.3"))
    resp = await auth_client.get("/api/config/export")
    assert resp.status_code == 200
    data = resp.json()
    assert data["device_count"] == 3
    assert len(data["devices"]) == 3


# ── Import ────────────────────────────────────────────────────────────────────

def _upload_json(data: dict) -> dict:
    """Build the files dict for httpx multipart upload."""
    raw = json.dumps(data).encode()
    return {"file": ("cloudshell-config.json", io.BytesIO(raw), "application/json")}


async def test_import_requires_auth(client):
    """Unauthenticated POST /api/config/import must return 401."""
    bundle = {"format_version": 1, "exported_at": "2026-01-01T00:00:00+00:00", "device_count": 0, "devices": []}
    resp = await client.post("/api/config/import", files=_upload_json(bundle))
    assert resp.status_code == 401


async def test_import_empty_bundle(auth_client):
    """Importing an empty bundle returns 0 imported, 0 skipped."""
    bundle = {"format_version": 1, "exported_at": "2026-01-01T00:00:00+00:00", "device_count": 0, "devices": []}
    resp = await auth_client.post("/api/config/import", files=_upload_json(bundle))
    assert resp.status_code == 200
    result = resp.json()
    assert result["imported"] == 0
    assert result["skipped"] == 0
    assert result["errors"] == 0


async def test_import_password_device(auth_client):
    """Importing a password-auth device creates it and re-encrypts the password."""
    bundle = {
        "format_version": 1,
        "exported_at": "2026-01-01T00:00:00+00:00",
        "device_count": 1,
        "devices": [
            {
                "name": "imported-srv",
                "hostname": "192.168.50.1",
                "port": 22,
                "username": "root",
                "auth_type": "password",
                "connection_type": "ssh",
                "password": "imported-pass",
                "private_key": None,
            }
        ],
    }
    resp = await auth_client.post("/api/config/import", files=_upload_json(bundle))
    assert resp.status_code == 200
    assert resp.json()["imported"] == 1

    # Verify the device exists and the password is correctly re-encrypted
    devices_resp = await auth_client.get("/api/devices/")
    devices = devices_resp.json()
    assert len(devices) == 1
    assert devices[0]["name"] == "imported-srv"


async def test_import_key_device(auth_client):
    """Importing a key-auth device creates it and stores the encrypted key file."""
    pem, _ = generate_key_pair()
    bundle = {
        "format_version": 1,
        "exported_at": "2026-01-01T00:00:00+00:00",
        "device_count": 1,
        "devices": [
            {
                "name": "key-imported",
                "hostname": "192.168.60.1",
                "port": 2222,
                "username": "deploy",
                "auth_type": "key",
                "connection_type": "sftp",
                "password": None,
                "private_key": pem,
            }
        ],
    }
    resp = await auth_client.post("/api/config/import", files=_upload_json(bundle))
    assert resp.status_code == 200
    assert resp.json()["imported"] == 1

    devices_resp = await auth_client.get("/api/devices/")
    devices = devices_resp.json()
    assert len(devices) == 1
    assert devices[0]["auth_type"] == "key"
    assert devices[0]["key_filename"] is not None

    # The key file must decrypt back to the original PEM
    settings = get_settings()
    recovered = load_decrypted_key(devices[0]["key_filename"], settings.keys_dir)
    assert recovered == pem


async def test_import_skips_duplicate(auth_client):
    """Device matching name+hostname+port+username+connection_type is not imported twice."""
    payload = _password_device()
    # Create device first
    await auth_client.post("/api/devices/", json=payload)

    bundle = {
        "format_version": 1,
        "exported_at": "2026-01-01T00:00:00+00:00",
        "device_count": 1,
        "devices": [
            {
                "name": payload["name"],          # same name
                "hostname": payload["hostname"],
                "port": payload["port"],
                "username": payload["username"],
                "auth_type": "password",
                "connection_type": "ssh",
                "password": "whatever",
                "private_key": None,
            }
        ],
    }
    resp = await auth_client.post("/api/config/import", files=_upload_json(bundle))
    assert resp.status_code == 200
    result = resp.json()
    assert result["imported"] == 0
    assert result["skipped"] == 1
    assert len(result["messages"]) == 1
    assert "already exists" in result["messages"][0]
    # Only the original device remains
    devices_resp = await auth_client.get("/api/devices/")
    assert len(devices_resp.json()) == 1


async def test_import_partial_duplicate(auth_client):
    """Only new devices are imported; existing ones are skipped."""
    pem, _ = generate_key_pair()
    existing_payload = _password_device(name="existing", hostname="10.0.0.10")
    await auth_client.post("/api/devices/", json=existing_payload)

    bundle = {
        "format_version": 1,
        "exported_at": "2026-01-01T00:00:00+00:00",
        "device_count": 2,
        "devices": [
            {
                "name": existing_payload["name"],  # same name — triggers skip
                "hostname": existing_payload["hostname"],
                "port": existing_payload["port"],
                "username": existing_payload["username"],
                "auth_type": "password",
                "connection_type": "ssh",
                "password": "pass",
                "private_key": None,
            },
            {
                "name": "brand-new",
                "hostname": "10.0.0.99",
                "port": 22,
                "username": "newuser",
                "auth_type": "password",
                "connection_type": "ssh",
                "password": "pass",
                "private_key": None,
            },
        ],
    }
    resp = await auth_client.post("/api/config/import", files=_upload_json(bundle))
    assert resp.status_code == 200
    result = resp.json()
    assert result["imported"] == 1
    assert result["skipped"] == 1

    devices_resp = await auth_client.get("/api/devices/")
    assert len(devices_resp.json()) == 2


async def test_import_invalid_json(auth_client):
    """Uploading a non-JSON file returns 400."""
    files = {"file": ("bad.json", io.BytesIO(b"not valid json"), "application/json")}
    resp = await auth_client.post("/api/config/import", files=files)
    assert resp.status_code == 400
    assert "Invalid export file" in resp.json()["detail"]


async def test_import_wrong_format_version(auth_client):
    """A bundle with an unsupported format_version returns 400."""
    bundle = {
        "format_version": 999,
        "exported_at": "2026-01-01T00:00:00+00:00",
        "device_count": 0,
        "devices": [],
    }
    resp = await auth_client.post("/api/config/import", files=_upload_json(bundle))
    assert resp.status_code == 400
    assert "Unsupported export format version" in resp.json()["detail"]


async def test_export_then_import_roundtrip(auth_client):
    """Export and re-import produces identical devices with correct credentials."""
    pem, _ = generate_key_pair()
    await auth_client.post("/api/devices/", json=_password_device(name="pass-srv", hostname="5.5.5.5"))
    await auth_client.post("/api/devices/", json=_key_device(pem, name="key-srv", hostname="6.6.6.6"))

    # Export
    export_resp = await auth_client.get("/api/config/export")
    assert export_resp.status_code == 200
    bundle_data = export_resp.json()

    # Delete all existing devices so the import has nothing to skip
    devices_resp = await auth_client.get("/api/devices/")
    for dev in devices_resp.json():
        await auth_client.delete(f"/api/devices/{dev['id']}")

    devices_after_delete = await auth_client.get("/api/devices/")
    assert devices_after_delete.json() == []

    # Import
    import_resp = await auth_client.post(
        "/api/config/import",
        files=_upload_json(bundle_data),
    )
    assert import_resp.status_code == 200
    result = import_resp.json()
    assert result["imported"] == 2
    assert result["skipped"] == 0
    assert result["errors"] == 0

    # Verify devices are present
    final_devices = (await auth_client.get("/api/devices/")).json()
    assert len(final_devices) == 2
    names = {d["name"] for d in final_devices}
    assert names == {"pass-srv", "key-srv"}

    # Verify the key-auth device has its key file and it decrypts to the original PEM
    settings = get_settings()
    key_dev = next(d for d in final_devices if d["auth_type"] == "key")
    recovered = load_decrypted_key(key_dev["key_filename"], settings.keys_dir)
    assert recovered == pem
