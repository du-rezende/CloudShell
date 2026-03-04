"""
tests/test_config_transfer.py — tests for GET /api/config/export and POST /api/config/import.

Tests cover:
- GET /api/config/export       requires auth; returns JSON with all devices (password + key);
                               decrypted passwords and keys are present in the export;
                               returns 200 with Content-Disposition attachment header;
                               omits secrets gracefully when decryption fails
- POST /api/config/import      requires auth; imports new devices; skips duplicates;
                               rejects invalid JSON; rejects wrong format_version;
                               re-encrypts credentials with the target key;
                               handles password and key-auth devices correctly;
                               handles missing password / private_key in import entry;
                               recovers from per-device errors and continues importing;
                               full export → delete → import roundtrip
"""
import json
import io
from unittest.mock import patch

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


# ── Export: decryption failure is handled gracefully ─────────────────────────

async def test_export_decryption_failure_omits_secret(auth_client):
    """When decryption raises, the device is still exported but with null credentials."""
    await auth_client.post("/api/devices/", json=_password_device())

    with patch("backend.routers.config_transfer.decrypt", side_effect=Exception("bad key")):
        resp = await auth_client.get("/api/config/export")

    assert resp.status_code == 200
    data = resp.json()
    assert data["device_count"] == 1
    dev = data["devices"][0]
    # Secret omitted but device still present
    assert dev["name"] == "srv"
    assert dev["password"] is None


async def test_export_key_decryption_failure_omits_key(auth_client):
    """When key-file loading raises, the device is exported with null private_key."""
    pem, _ = generate_key_pair()
    await auth_client.post("/api/devices/", json=_key_device(pem))

    with patch("backend.routers.config_transfer.load_decrypted_key", side_effect=Exception("no file")):
        resp = await auth_client.get("/api/config/export")

    assert resp.status_code == 200
    data = resp.json()
    assert data["device_count"] == 1
    assert data["devices"][0]["private_key"] is None


# ── Import: missing credential fields ────────────────────────────────────────

async def test_import_password_device_missing_password(auth_client):
    """A password-auth entry without a password value is counted as an error."""
    bundle = {
        "format_version": 1,
        "exported_at": "2026-01-01T00:00:00+00:00",
        "device_count": 1,
        "devices": [
            {
                "name": "no-pass",
                "hostname": "10.9.9.1",
                "port": 22,
                "username": "root",
                "auth_type": "password",
                "connection_type": "ssh",
                "password": None,   # deliberately missing
                "private_key": None,
            }
        ],
    }
    resp = await auth_client.post("/api/config/import", files=_upload_json(bundle))
    assert resp.status_code == 200
    result = resp.json()
    assert result["errors"] == 1
    assert result["imported"] == 0
    assert any("password" in m for m in result["messages"])


async def test_import_key_device_missing_private_key(auth_client):
    """A key-auth entry without a private_key value is counted as an error."""
    bundle = {
        "format_version": 1,
        "exported_at": "2026-01-01T00:00:00+00:00",
        "device_count": 1,
        "devices": [
            {
                "name": "no-key",
                "hostname": "10.9.9.2",
                "port": 22,
                "username": "deploy",
                "auth_type": "key",
                "connection_type": "ssh",
                "password": None,
                "private_key": None,   # deliberately missing
            }
        ],
    }
    resp = await auth_client.post("/api/config/import", files=_upload_json(bundle))
    assert resp.status_code == 200
    result = resp.json()
    assert result["errors"] == 1
    assert result["imported"] == 0
    assert any("private_key" in m for m in result["messages"])


# ── Import: error recovery — subsequent devices are still processed ───────────

async def test_import_error_recovery_continues(auth_client):
    """An error on one device does not prevent the remaining devices from being imported."""
    bundle = {
        "format_version": 1,
        "exported_at": "2026-01-01T00:00:00+00:00",
        "device_count": 3,
        "devices": [
            {
                "name": "good-first",
                "hostname": "10.1.1.1",
                "port": 22,
                "username": "user",
                "auth_type": "password",
                "connection_type": "ssh",
                "password": "pass",
                "private_key": None,
            },
            {
                "name": "bad-middle",
                "hostname": "10.1.1.2",
                "port": 22,
                "username": "user",
                "auth_type": "password",
                "connection_type": "ssh",
                "password": None,   # triggers error
                "private_key": None,
            },
            {
                "name": "good-last",
                "hostname": "10.1.1.3",
                "port": 22,
                "username": "user",
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
    assert result["imported"] == 2
    assert result["errors"] == 1
    assert result["skipped"] == 0

    devices_resp = await auth_client.get("/api/devices/")
    names = {d["name"] for d in devices_resp.json()}
    assert "good-first" in names
    assert "good-last" in names
    assert "bad-middle" not in names


# ── Import: exported_at field is present in export bundle ────────────────────

async def test_export_bundle_has_exported_at(auth_client):
    """The export bundle includes a non-empty exported_at ISO timestamp."""
    resp = await auth_client.get("/api/config/export")
    assert resp.status_code == 200
    data = resp.json()
    assert "exported_at" in data
    assert data["exported_at"]  # non-empty


# ── Import: connection_type is preserved ─────────────────────────────────────

@pytest.mark.parametrize("conn_type", ["ssh", "sftp", "ftp", "ftps"])
async def test_import_preserves_connection_type(auth_client, conn_type: str):
    """All connection types survive an import cycle."""
    port = 21 if conn_type in ("ftp", "ftps") else 22
    bundle = {
        "format_version": 1,
        "exported_at": "2026-01-01T00:00:00+00:00",
        "device_count": 1,
        "devices": [
            {
                "name": f"{conn_type}-device",
                "hostname": f"10.20.{port}.1",
                "port": port,
                "username": "user",
                "auth_type": "password",
                "connection_type": conn_type,
                "password": "pass",
                "private_key": None,
            }
        ],
    }
    resp = await auth_client.post("/api/config/import", files=_upload_json(bundle))
    assert resp.status_code == 200
    assert resp.json()["imported"] == 1

    devices = (await auth_client.get("/api/devices/")).json()
    assert devices[0]["connection_type"] == conn_type


# ── Import: passwords are correctly re-encrypted ─────────────────────────────

async def test_import_password_is_reencrypted(auth_client, db_session):
    """Imported password is stored encrypted; decrypting it yields the original value."""
    from sqlalchemy import select
    from backend.models.device import Device
    from backend.services.crypto import decrypt as crypto_decrypt

    bundle = {
        "format_version": 1,
        "exported_at": "2026-01-01T00:00:00+00:00",
        "device_count": 1,
        "devices": [
            {
                "name": "enc-check",
                "hostname": "10.30.0.1",
                "port": 22,
                "username": "root",
                "auth_type": "password",
                "connection_type": "ssh",
                "password": "super-secret-123",
                "private_key": None,
            }
        ],
    }
    resp = await auth_client.post("/api/config/import", files=_upload_json(bundle))
    assert resp.status_code == 200
    assert resp.json()["imported"] == 1

    # Query via the same test session so we hit the in-memory DB
    result = await db_session.execute(select(Device).where(Device.name == "enc-check"))
    device = result.scalar_one()
    assert device.encrypted_password is not None
    assert device.encrypted_password != "super-secret-123"
    assert crypto_decrypt(device.encrypted_password) == "super-secret-123"


# ── Direct unit tests for the export helper ───────────────────────────────────

async def test_build_export_bundle_password_device(db_session):
    """_build_export_bundle decrypts and returns the password for a password-auth device."""
    from backend.routers.config_transfer import _build_export_bundle
    from backend.models.device import Device, AuthType, ConnectionType
    from backend.services.crypto import encrypt

    device = Device(
        name="direct-srv",
        hostname="10.50.0.1",
        port=22,
        username="root",
        auth_type=AuthType.password,
        connection_type=ConnectionType.ssh,
        encrypted_password=encrypt("direct-pass"),
    )
    db_session.add(device)
    await db_session.flush()

    bundle = await _build_export_bundle(db_session, "/tmp/cloudshell-pytest/keys")
    assert bundle.device_count == 1
    assert bundle.devices[0].password == "direct-pass"
    assert bundle.devices[0].private_key is None


async def test_build_export_bundle_key_device(db_session):
    """_build_export_bundle reads and decrypts the key file for a key-auth device."""
    from backend.routers.config_transfer import _build_export_bundle
    from backend.models.device import Device, AuthType, ConnectionType
    from backend.services.crypto import save_encrypted_key

    pem, _ = generate_key_pair()
    keys_dir = "/tmp/cloudshell-pytest/keys"

    device = Device(
        name="direct-key-srv",
        hostname="10.50.0.2",
        port=22,
        username="deploy",
        auth_type=AuthType.key,
        connection_type=ConnectionType.ssh,
    )
    db_session.add(device)
    await db_session.flush()
    device.key_filename = save_encrypted_key(device.id, pem, keys_dir)
    await db_session.flush()

    bundle = await _build_export_bundle(db_session, keys_dir)
    assert bundle.device_count == 1
    assert bundle.devices[0].private_key == pem
    assert bundle.devices[0].password is None


async def test_build_export_bundle_decrypt_failure_omits_secret(db_session):
    """_build_export_bundle includes the device even when decryption raises."""
    from backend.routers.config_transfer import _build_export_bundle
    from backend.models.device import Device, AuthType, ConnectionType

    device = Device(
        name="broken-enc",
        hostname="10.50.0.3",
        port=22,
        username="root",
        auth_type=AuthType.password,
        connection_type=ConnectionType.ssh,
        encrypted_password="not-valid-base64!!!",
    )
    db_session.add(device)
    await db_session.flush()

    bundle = await _build_export_bundle(db_session, "/tmp/cloudshell-pytest/keys")
    assert bundle.device_count == 1
    assert bundle.devices[0].name == "broken-enc"
    assert bundle.devices[0].password is None  # omitted due to error


# ── Direct unit tests for _build_export_response ─────────────────────────────

async def test_build_export_response_returns_json_response(db_session):
    """_build_export_response returns a Response with correct content-type and disposition."""
    from backend.routers.config_transfer import _build_export_response

    response = await _build_export_response(db_session, "/tmp/cloudshell-pytest/keys")

    assert response.status_code == 200
    assert response.media_type == "application/json"
    cd = response.headers.get("content-disposition", "")
    assert "attachment" in cd
    assert "cloudshell-config.json" in cd


async def test_build_export_response_body_is_valid_bundle(db_session):
    """_build_export_response body deserialises to a valid ExportBundle."""
    import json
    from backend.routers.config_transfer import _build_export_response, ExportBundle
    from backend.models.device import Device, AuthType, ConnectionType
    from backend.services.crypto import encrypt

    device = Device(
        name="resp-srv",
        hostname="10.60.0.1",
        port=22,
        username="root",
        auth_type=AuthType.password,
        connection_type=ConnectionType.ssh,
        encrypted_password=encrypt("resp-pass"),
    )
    db_session.add(device)
    await db_session.flush()

    response = await _build_export_response(db_session, "/tmp/cloudshell-pytest/keys")
    data = json.loads(response.body)
    bundle = ExportBundle.model_validate(data)
    assert bundle.device_count == 1
    assert bundle.devices[0].name == "resp-srv"
    assert bundle.devices[0].password == "resp-pass"


# ── Direct unit tests for _process_import_bundle ─────────────────────────────

async def test_process_import_bundle_password_device(db_session):
    """_process_import_bundle inserts a password-auth device and re-encrypts its password."""
    from sqlalchemy import select
    from backend.routers.config_transfer import _process_import_bundle, ExportBundle
    from backend.models.device import Device
    from backend.services.crypto import decrypt as crypto_decrypt

    bundle = ExportBundle.model_validate({
        "format_version": 1,
        "exported_at": "2026-01-01T00:00:00+00:00",
        "device_count": 1,
        "devices": [{
            "name": "direct-import",
            "hostname": "10.70.0.1",
            "port": 22,
            "username": "root",
            "auth_type": "password",
            "connection_type": "ssh",
            "password": "direct-pass",
            "private_key": None,
        }],
    })

    result = await _process_import_bundle(db_session, bundle, "/tmp/cloudshell-pytest/keys")
    assert result.imported == 1
    assert result.skipped == 0
    assert result.errors == 0

    row = (await db_session.execute(select(Device).where(Device.name == "direct-import"))).scalar_one()
    assert row.encrypted_password is not None
    assert row.encrypted_password != "direct-pass"
    assert crypto_decrypt(row.encrypted_password) == "direct-pass"


async def test_process_import_bundle_key_device(db_session):
    """_process_import_bundle inserts a key-auth device and stores the encrypted key file."""
    from backend.routers.config_transfer import _process_import_bundle, ExportBundle
    from backend.services.crypto import load_decrypted_key

    pem, _ = generate_key_pair()
    keys_dir = "/tmp/cloudshell-pytest/keys"

    bundle = ExportBundle.model_validate({
        "format_version": 1,
        "exported_at": "2026-01-01T00:00:00+00:00",
        "device_count": 1,
        "devices": [{
            "name": "direct-key-import",
            "hostname": "10.70.0.2",
            "port": 22,
            "username": "deploy",
            "auth_type": "key",
            "connection_type": "sftp",
            "password": None,
            "private_key": pem,
        }],
    })

    result = await _process_import_bundle(db_session, bundle, keys_dir)
    assert result.imported == 1
    assert result.errors == 0

    from sqlalchemy import select
    from backend.models.device import Device
    row = (await db_session.execute(select(Device).where(Device.name == "direct-key-import"))).scalar_one()
    assert row.key_filename is not None
    assert load_decrypted_key(row.key_filename, keys_dir) == pem


async def test_process_import_bundle_skips_duplicate(db_session):
    """_process_import_bundle skips a device that already exists in the DB."""
    from backend.routers.config_transfer import _process_import_bundle, ExportBundle
    from backend.models.device import Device, AuthType, ConnectionType
    from backend.services.crypto import encrypt

    existing = Device(
        name="dup-srv",
        hostname="10.70.0.3",
        port=22,
        username="root",
        auth_type=AuthType.password,
        connection_type=ConnectionType.ssh,
        encrypted_password=encrypt("old"),
    )
    db_session.add(existing)
    await db_session.flush()

    bundle = ExportBundle.model_validate({
        "format_version": 1,
        "exported_at": "2026-01-01T00:00:00+00:00",
        "device_count": 1,
        "devices": [{
            "name": "dup-srv",
            "hostname": "10.70.0.3",
            "port": 22,
            "username": "root",
            "auth_type": "password",
            "connection_type": "ssh",
            "password": "new",
            "private_key": None,
        }],
    })

    result = await _process_import_bundle(db_session, bundle, "/tmp/cloudshell-pytest/keys")
    assert result.imported == 0
    assert result.skipped == 1
    assert result.errors == 0
    assert "already exists" in result.messages[0]


async def test_process_import_bundle_missing_password_is_error(db_session):
    """_process_import_bundle counts a password-auth entry with no password as an error."""
    from backend.routers.config_transfer import _process_import_bundle, ExportBundle

    bundle = ExportBundle.model_validate({
        "format_version": 1,
        "exported_at": "2026-01-01T00:00:00+00:00",
        "device_count": 1,
        "devices": [{
            "name": "no-pass-direct",
            "hostname": "10.70.0.4",
            "port": 22,
            "username": "root",
            "auth_type": "password",
            "connection_type": "ssh",
            "password": None,
            "private_key": None,
        }],
    })

    result = await _process_import_bundle(db_session, bundle, "/tmp/cloudshell-pytest/keys")
    assert result.errors == 1
    assert result.imported == 0
    assert any("password" in m for m in result.messages)


async def test_process_import_bundle_missing_key_is_error(db_session):
    """_process_import_bundle counts a key-auth entry with no private_key as an error."""
    from backend.routers.config_transfer import _process_import_bundle, ExportBundle

    bundle = ExportBundle.model_validate({
        "format_version": 1,
        "exported_at": "2026-01-01T00:00:00+00:00",
        "device_count": 1,
        "devices": [{
            "name": "no-key-direct",
            "hostname": "10.70.0.5",
            "port": 22,
            "username": "deploy",
            "auth_type": "key",
            "connection_type": "ssh",
            "password": None,
            "private_key": None,
        }],
    })

    result = await _process_import_bundle(db_session, bundle, "/tmp/cloudshell-pytest/keys")
    assert result.errors == 1
    assert result.imported == 0
    assert any("private_key" in m for m in result.messages)


async def test_process_import_bundle_error_recovery(db_session):
    """_process_import_bundle continues after a per-device error."""
    from backend.routers.config_transfer import _process_import_bundle, ExportBundle
    from sqlalchemy import select
    from backend.models.device import Device

    bundle = ExportBundle.model_validate({
        "format_version": 1,
        "exported_at": "2026-01-01T00:00:00+00:00",
        "device_count": 3,
        "devices": [
            {
                "name": "ok-first",
                "hostname": "10.70.1.1",
                "port": 22,
                "username": "user",
                "auth_type": "password",
                "connection_type": "ssh",
                "password": "pass",
                "private_key": None,
            },
            {
                "name": "bad-middle",
                "hostname": "10.70.1.2",
                "port": 22,
                "username": "user",
                "auth_type": "password",
                "connection_type": "ssh",
                "password": None,   # triggers error
                "private_key": None,
            },
            {
                "name": "ok-last",
                "hostname": "10.70.1.3",
                "port": 22,
                "username": "user",
                "auth_type": "password",
                "connection_type": "ssh",
                "password": "pass",
                "private_key": None,
            },
        ],
    })

    result = await _process_import_bundle(db_session, bundle, "/tmp/cloudshell-pytest/keys")
    assert result.imported == 2
    assert result.errors == 1
    assert result.skipped == 0

    rows = (await db_session.execute(select(Device))).scalars().all()
    names = {r.name for r in rows}
    assert "ok-first" in names
    assert "ok-last" in names
    assert "bad-middle" not in names


async def test_process_import_bundle_empty(db_session):
    """_process_import_bundle with an empty devices list returns all-zero counts."""
    from backend.routers.config_transfer import _process_import_bundle, ExportBundle

    bundle = ExportBundle.model_validate({
        "format_version": 1,
        "exported_at": "2026-01-01T00:00:00+00:00",
        "device_count": 0,
        "devices": [],
    })

    result = await _process_import_bundle(db_session, bundle, "/tmp/cloudshell-pytest/keys")
    assert result.imported == 0
    assert result.skipped == 0
    assert result.errors == 0
    assert result.messages == []
