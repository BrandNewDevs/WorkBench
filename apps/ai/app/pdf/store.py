"""Owner-scoped PDF state and exact approval claims in the application SQLite database."""

import json
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import aiosqlite

from app.pdf.contracts import PdfDocumentDraft, PdfEditPlan, PdfSessionView
from app.storage.sqlite import LocalSQLiteDatabase


def plan_hash(plan: PdfDocumentDraft | PdfEditPlan) -> str:
    return sha256(
        json.dumps(
            plan.model_dump(mode="json", by_alias=True), sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


class PdfStateStore:
    def __init__(self, database: LocalSQLiteDatabase) -> None:
        self.database = database

    async def initialize(self) -> None:
        async with self.database.open() as connection:
            await connection.execute("""CREATE TABLE IF NOT EXISTS pdf_activity (
                sequence INTEGER PRIMARY KEY, session_id TEXT NOT NULL
                REFERENCES workflow_sessions(session_id), label TEXT NOT NULL)""")
            await connection.execute("""CREATE TABLE IF NOT EXISTS pdf_sessions (
                session_id TEXT PRIMARY KEY REFERENCES workflow_sessions(session_id),
                state TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 0
            )""")
            await connection.execute("""CREATE TABLE IF NOT EXISTS pdf_write_claims (
                approval_id TEXT PRIMARY KEY, session_id TEXT NOT NULL
                    REFERENCES workflow_sessions(session_id),
                plan_hash TEXT NOT NULL, destination TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('executing','completed','failed'))
            )""")
            # Interrupted exports require a fresh proposal; never replay a claimed write.
            await connection.execute(
                "UPDATE pdf_write_claims SET status='failed' WHERE status='executing'"
            )

    async def get(self, session_id: UUID, owner_id: UUID) -> PdfSessionView:
        async with self.database.open() as connection:
            owned = await (
                await connection.execute(
                    "SELECT 1 FROM workflow_sessions WHERE session_id=? AND owner_user_id=? "
                    "AND workflow_type='pdfDocument' AND status='active'",
                    (str(session_id), str(owner_id)),
                )
            ).fetchone()
            if owned is None:
                raise PermissionError("PDF session not found")
            row = await (
                await connection.execute(
                    "SELECT state FROM pdf_sessions WHERE session_id=?",
                    (str(session_id),),
                )
            ).fetchone()
            events = await (
                await connection.execute(
                    "SELECT label FROM pdf_activity WHERE session_id=? "
                    "ORDER BY sequence DESC LIMIT 100",
                    (str(session_id),),
                )
            ).fetchall()
            failed = await (
                await connection.execute(
                    "SELECT approval_id FROM pdf_write_claims "
                    "WHERE session_id=? AND status='failed'",
                    (str(session_id),),
                )
            ).fetchall()
        state = PdfSessionView.model_validate_json(row[0]) if row else PdfSessionView()
        failed_ids = {entry[0] for entry in failed}
        turns = tuple(
            turn.model_copy(
                update={"approval": turn.approval.model_copy(update={"status": "failed"})}
            )
            if turn.approval and str(turn.approval.approval_id) in failed_ids
            else turn
            for turn in state.turns
        )
        return state.model_copy(
            update={"activity": tuple(event[0] for event in reversed(list(events))), "turns": turns}
        )

    async def record_activity(self, session_id: UUID, label: str) -> None:
        async with self.database.open() as connection:
            await connection.execute(
                "INSERT INTO pdf_activity(session_id,label) VALUES(?,?)", (str(session_id), label)
            )

    async def save(self, session_id: UUID, owner_id: UUID, state: PdfSessionView) -> None:
        async with self.database.open() as connection:
            await connection.execute("BEGIN IMMEDIATE")
            await self._save_state(connection, session_id, owner_id, state)

    async def claim(
        self, approval_id: UUID, session_id: UUID, owner_id: UUID, digest: str, destination: Path
    ) -> bool:
        async with self.database.open() as connection:
            await connection.execute("BEGIN IMMEDIATE")
            row = await (
                await connection.execute(
                    "SELECT p.state FROM pdf_sessions p JOIN workflow_sessions s USING(session_id) "
                    "WHERE session_id=? AND owner_user_id=? AND s.status='active'",
                    (str(session_id), str(owner_id)),
                )
            ).fetchone()
            if row is None:
                return False
            state = PdfSessionView.model_validate_json(row[0])
            pending = state.turns[-1].approval if state.turns else None
            if (
                pending is None
                or pending.approval_id != approval_id
                or pending.status != "pending"
                or pending.arguments_hash != digest
            ):
                return False
            cursor = await connection.execute(
                "INSERT OR IGNORE INTO pdf_write_claims VALUES(?,?,?,?,'executing')",
                (str(approval_id), str(session_id), digest, str(destination.resolve())),
            )
            return cursor.rowcount == 1

    async def finalize_claim(
        self,
        approval_id: UUID,
        session_id: UUID,
        owner_id: UUID,
        state: PdfSessionView,
        *,
        succeeded: bool,
    ) -> None:
        """Persist claim outcome and matching turn state in one transaction."""
        async with self.database.open() as connection:
            await connection.execute("BEGIN IMMEDIATE")
            cursor = await connection.execute(
                "UPDATE pdf_write_claims SET status=? "
                "WHERE approval_id=? AND session_id=? AND status='executing'",
                (
                    "completed" if succeeded else "failed",
                    str(approval_id),
                    str(session_id),
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("PDF execution claim is no longer current")
            await self._save_state(connection, session_id, owner_id, state)

    @staticmethod
    async def _save_state(
        connection: aiosqlite.Connection,
        session_id: UUID,
        owner_id: UUID,
        state: PdfSessionView,
    ) -> None:
        owned = await (
            await connection.execute(
                "SELECT 1 FROM workflow_sessions WHERE session_id=? AND owner_user_id=? "
                "AND workflow_type='pdfDocument' AND status='active'",
                (str(session_id), str(owner_id)),
            )
        ).fetchone()
        if owned is None:
            raise PermissionError("PDF session not found")
        await connection.execute(
            "INSERT INTO pdf_sessions(session_id,state) VALUES(?,?) "
            "ON CONFLICT(session_id) DO UPDATE SET state=excluded.state, version=version+1",
            (str(session_id), state.model_dump_json(by_alias=True)),
        )
        pending = state.turns[-1].approval if state.turns else None
        stage = "awaitingApproval" if pending and pending.status == "pending" else "ready"
        if not state.pages and not state.turns:
            stage = "collectingInputs"
        await connection.execute(
            "UPDATE workflow_sessions SET stage=?, "
            "updated_at=strftime('%Y-%m-%dT%H:%M:%f+00:00','now') "
            "WHERE session_id=?",
            (stage, str(session_id)),
        )

    def write_policy(
        self,
        approval_id: UUID,
        session_id: UUID,
        plan: PdfDocumentDraft | PdfEditPlan,
        destination: Path,
    ) -> bool:
        # Rendering runs in a worker thread. Recheck the persisted one-time claim
        # immediately before the side effect; model/client fields cannot grant approval.
        import sqlite3

        with sqlite3.connect(self.database.database_path) as connection:
            return (
                connection.execute(
                    "SELECT 1 FROM pdf_write_claims WHERE approval_id=? AND session_id=? "
                    "AND plan_hash=? AND destination=? AND status='executing'",
                    (
                        str(approval_id),
                        str(session_id),
                        plan_hash(plan),
                        str(destination.resolve()),
                    ),
                ).fetchone()
                is not None
            )
