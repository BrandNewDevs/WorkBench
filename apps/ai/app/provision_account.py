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
    return parser.parse_args()


def _database_path(arguments: argparse.Namespace) -> Path:
    return arguments.database_path or ApplicationSettings().database_path


def _render_accounts(rows: list[aiosqlite.Row]) -> str:
    headers = ("user id", "username", "display name", "role", "status")
    records = [
        (
            row["user_id"],
            row["username"],
            row["display_name"],
            row["role"],
            "disabled" if row["disabled"] else "active",
        )
        for row in rows
    ]
    widths = [
        max(len(headers[index]), *(len(record[index]) for record in records))
        for index in range(len(headers))
    ]
    lines = ["  ".join(header.ljust(widths[index]) for index, header in enumerate(headers))]
    lines.append("  ".join("-" * width for width in widths))
    for record in records:
        lines.append("  ".join(value.ljust(widths[index]) for index, value in enumerate(record)))
    return "\n".join(lines)


async def _list_accounts(database_path: Path) -> int:
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
            """SELECT user_id, username, display_name, role, disabled
            FROM identities ORDER BY username"""
        )
        rows = list(await cursor.fetchall())

    if not rows:
        print(f"No accounts are provisioned in {database.database_path}.", file=sys.stderr)
        return 1

    print(f"Provisioned accounts in {database.database_path}:")
    print()
    print(_render_accounts(rows))
    return 0


async def _run(arguments: argparse.Namespace) -> int:
    if arguments.list_accounts:
        return await _list_accounts(_database_path(arguments))

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
