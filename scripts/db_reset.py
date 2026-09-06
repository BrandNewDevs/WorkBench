"""Clear every application-owned table in a WorkBench SQLite database.

Development helper: resets identities, sessions, chat, approvals, artifacts,
and audit rows while keeping the schema intact. Prints a summary when done,
then hints that the next step is provisioning a fresh initial account.
"""

import sqlite3
import sys
import os

TABLE_QUERY = "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
IDENTITIES_QUERY = "SELECT count(*) FROM identities"


def main() -> None:
    """Drop all rows from application tables in the argument-named database."""

    database_path = sys.argv[1]
    with sqlite3.connect(database_path) as connection:
        tables = [row[0] for row in connection.execute(TABLE_QUERY)]
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("BEGIN")
        for table in tables:
            connection.execute(f'DELETE FROM "{table}"')
        connection.commit()
        print(f"Cleared {len(tables)} tables in {os.path.expanduser(database_path)}")
        print(f"Identities remaining: {connection.execute(IDENTITIES_QUERY).fetchone()[0]}")
    print("Provision an account with pnpm account:provision")


if __name__ == "__main__":
    main()
