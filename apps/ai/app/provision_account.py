"""Interactive command for provisioning the first local employee account."""

import argparse
import asyncio
import getpass
import sys
from pathlib import Path

from app.auth.provisioning import InitialAccountAlreadyProvisionedError, provision_initial_employee
from app.config import ApplicationSettings


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Provision the first WorkBench employee account.")
    parser.add_argument(
        "--database-path", type=Path, help="Override the local SQLite database path."
    )
    return parser.parse_args()


async def _run() -> int:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print(
            "Initial account provisioning requires an interactive local terminal.", file=sys.stderr
        )
        return 2

    arguments = _arguments()
    username = input("Employee username: ")
    display_name = input("Employee display name: ")
    password = getpass.getpass("New password: ")
    confirmation = getpass.getpass("Confirm new password: ")
    if password != confirmation:
        print("Passwords do not match.", file=sys.stderr)
        return 2

    database_path = arguments.database_path or ApplicationSettings().database_path
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
    """Run the interactive local provisioning command."""

    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
