"""Local command for provisioning and inspecting the first employee account."""

import argparse
import asyncio
import getpass
import sys
from pathlib import Path

import aiosqlite

from app.auth.provisioning import InitialAccountAlreadyProvisionedError, provision_initial_employee
from app.config import ApplicationSettings
from app.storage.sqlite import LocalSQLiteDatabase


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Provision the first WorkBench employee account.")
    parser.add_argument(
        "--database-path", type=Path, help="Override the local SQLite database path."
    )
    parser.add_argument(
        "--list",
        dest="list_accounts",
        action="store_true",
        help="Show provisioned accounts without secrets and exit; read-only.",
    )
    parser.add_argument(
        "--show-secrets",
        dest="show_secrets",
        action="store_true",
        help="Include password hashes and live session token IDs with --list; development only.",
    )
    return parser.parse_args()


def _database_path(arguments: argparse.Namespace) -> Path:
    return arguments.database_path or ApplicationSettings().database_path


def _render_accounts(rows: list[aiosqlite.Row], show_secrets: bool) -> str:
    headers: tuple[str, ...] = ("user id", "username", "display name", "role", "status")
    if show_secrets:
        headers = (*headers, "password hash")
    records: list[tuple[str, ...]] = []
    for row in rows:
        record: tuple[str, ...] = (
            row["user_id"],
            row["username"],
            row["display_name"],
            row["role"],
            "disabled" if row["disabled"] else "active",
        )
        if show_secrets:
            record = (*record, row["password_hash"])
        records.append(record)
    widths = [
        max(len(headers[index]), *(len(record[index]) for record in records))
        for index in range(len(headers))
    ]
    lines = ["  ".join(header.ljust(widths[index]) for index, header in enumerate(headers))]
    lines.append("  ".join("-" * width for width in widths))
    for record in records:
        lines.append("  ".join(value.ljust(widths[index]) for index, value in enumerate(record)))
    return "\n".join(lines)


async def _list_accounts(database_path: Path, show_secrets: bool = False) -> int:
    try:
        database = LocalSQLiteDatabase(database_path)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2

    if not database.database_path.exists():
        print(f"No WorkBench database exists yet at {database.database_path}.", file=sys.stderr)
        return 1

    await database.initialize()
    async with database.open() as connection:
        cursor = await connection.execute(
            """SELECT user_id, username, display_name, role, disabled, password_hash
            FROM identities ORDER BY username"""
        )
        rows = list(await cursor.fetchall())
        session_cursor = await connection.execute(
            """SELECT username, token_id, expires_at, revoked_at
            FROM auth_sessions
            JOIN identities ON identities.user_id = auth_sessions.user_id
            ORDER BY username, expires_at"""
        )
        session_rows = list(await session_cursor.fetchall())

    if not rows:
        print(f"No accounts are provisioned in {database.database_path}.", file=sys.stderr)
        return 1

    print(f"Provisioned accounts in {database.database_path}:")
    print()
    print(_render_accounts(rows, show_secrets))
    if show_secrets:
        print()
        print("Auth sessions:")
        if session_rows:
            for session_row in session_rows:
                print(
                    f"  {session_row['username']}  {session_row['token_id']}  "
                    f"expires={session_row['expires_at']}  revoked={session_row['revoked_at']}"
                )
        else:
            print("  none")
    return 0


async def _run(arguments: argparse.Namespace) -> int:
    if arguments.list_accounts:
        return await _list_accounts(
            _database_path(arguments), getattr(arguments, "show_secrets", False)
        )

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print(
            "Initial account provisioning requires an interactive local terminal.", file=sys.stderr
        )
        return 2

    username = input("Employee username: ")
    display_name = input("Employee display name: ")
    password = getpass.getpass("New password: ")
    confirmation = getpass.getpass("Confirm new password: ")
    if password != confirmation:
        print("Passwords do not match.", file=sys.stderr)
        return 2

    database_path = _database_path(arguments)
    try:
        await provision_initial_employee(
            database_path=database_path,
            username=username,
            display_name=display_name,
            password=password,
        )
    except (InitialAccountAlreadyProvisionedError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2

    print(f"Provisioned the initial employee account in {database_path.expanduser()}.")
    return 0


def main() -> None:
    """Run the local provisioning and account-listing command."""

    arguments = _arguments()
    raise SystemExit(asyncio.run(_run(arguments)))


if __name__ == "__main__":
    main()
