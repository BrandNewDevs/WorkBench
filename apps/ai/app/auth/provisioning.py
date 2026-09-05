"""One-time local employee account provisioning for a new WorkBench database."""

from pathlib import Path
from uuid import uuid4

from pwdlib import PasswordHash
from pydantic import ValidationError

from app.api.auth_contracts import EmployeeLoginRequest
from app.auth.contracts import UserRole
from app.storage.sqlite import LocalSQLiteDatabase


class InitialAccountAlreadyProvisionedError(RuntimeError):
    """Raised when a database already has an identity."""


async def provision_initial_employee(
    *, database_path: Path, username: str, display_name: str, password: str
) -> None:
    """Create the first employee only when the local identity store is empty.

    This function intentionally has no HTTP route. The caller must be a local setup
    command, and the transaction prevents concurrent setup attempts from creating
    more than one initial account.
    """

    try:
        normalized_username = EmployeeLoginRequest(username=username, password=password).username
    except ValidationError as error:
        raise ValueError("Username and password must meet the local login requirements.") from error
    normalized_display_name = display_name.strip()
    if not normalized_display_name or len(normalized_display_name) > 200:
        raise ValueError("Display name must contain between 1 and 200 characters.")
    if len(password) < 12:
        raise ValueError("Initial account passwords must contain at least 12 characters.")

    database = LocalSQLiteDatabase(database_path)
    await database.initialize()
    password_hash = PasswordHash.recommended().hash(password)
    async with database.open() as connection:
        await connection.execute("BEGIN IMMEDIATE")
        cursor = await connection.execute("SELECT 1 FROM identities LIMIT 1")
        if await cursor.fetchone() is not None:
            raise InitialAccountAlreadyProvisionedError(
                "An account already exists. Initial provisioning can run only on an empty database."
            )
        await connection.execute(
            """INSERT INTO identities
            (user_id, username, display_name, role, password_hash, disabled)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (
                str(uuid4()),
                normalized_username,
                normalized_display_name,
                UserRole.EMPLOYEE.value,
                password_hash,
                0,
            ),
        )
