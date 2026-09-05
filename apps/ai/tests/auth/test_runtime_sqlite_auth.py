"""Runtime composition tests for SQLite-backed employee authentication."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.auth.provisioning import (
    InitialAccountAlreadyProvisionedError,
    provision_initial_employee,
)
from app.config import ApplicationSettings
from app.main import create_app
from app.storage import LocalSQLiteDatabase

ORIGIN = "http://127.0.0.1:5173"
SECRET = "runtime-test-signing-secret-material-at-least-forty-eight-bytes"
PASSWORD = "correct horse battery staple"


async def test_runtime_composition_provisions_and_persists_auth_lifecycle(tmp_path: Path) -> None:
    database_path = tmp_path / "state" / "workbench.db"
    await provision_initial_employee(
        database_path=database_path,
        username="Engineer.One",
        display_name="Engineer One",
        password=PASSWORD,
    )
    with pytest.raises(InitialAccountAlreadyProvisionedError):
        await provision_initial_employee(
            database_path=database_path,
            username="second.employee",
            display_name="Second Employee",
            password=PASSWORD,
        )
    settings = ApplicationSettings(
        auth_signing_secret=SECRET,
        database_path=database_path,
    )

    with TestClient(create_app(settings=settings)) as client:
        login = client.post(
            "/auth/login",
            headers={"Origin": ORIGIN},
            json={"username": "engineer.one", "password": PASSWORD},
        )
        restored = client.get("/auth/session", headers={"Origin": ORIGIN})
        logout = client.post("/auth/logout", headers={"Origin": ORIGIN})
        rejected_null_origin = client.post(
            "/auth/login",
            headers={"Origin": "null"},
            json={"username": "engineer.one", "password": PASSWORD},
        )

    assert login.status_code == restored.status_code == logout.status_code == 200
    assert restored.json() == login.json()
    assert logout.json() == {"revoked": True}
    assert rejected_null_origin.status_code == 403

    database = LocalSQLiteDatabase(database_path)
    async with database.open() as connection:
        session_cursor = await connection.execute("SELECT revoked_at FROM auth_sessions")
        sessions = list(await session_cursor.fetchall())
        audit_cursor = await connection.execute("SELECT outcome FROM audit_records ORDER BY rowid")
        audits = list(await audit_cursor.fetchall())

    assert sessions[0]["revoked_at"] is not None
    assert [row["outcome"] for row in audits] == ["loginSucceeded", "logoutRevoked"]
