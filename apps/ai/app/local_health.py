"""Sanitized readiness for locally composed persistence and capabilities."""

import shutil

from app.config import ApplicationSettings
from app.ports.local_backend import SubsystemReadiness, SystemHealthReport
from app.storage import LocalSQLiteDatabase


class LocalSystemHealthProvider:
    def __init__(self, database: LocalSQLiteDatabase, settings: ApplicationSettings) -> None:
        self._database = database
        self._settings = settings

    async def health(self) -> SystemHealthReport:
        try:
            async with self._database.open() as connection:
                await connection.execute("SELECT 1")
            storage = SubsystemReadiness(ready=True)
            audit = SubsystemReadiness(ready=True)
        except Exception:
            storage = SubsystemReadiness(ready=False, detail="Local persistence unavailable")
            audit = SubsystemReadiness(ready=False, detail="Local audit unavailable")
        docker_ready = shutil.which(self._settings.docker_executable) is not None
        return SystemHealthReport(
            storage=storage,
            sandbox=SubsystemReadiness(
                ready=docker_ready,
                detail=None if docker_ready else "Docker capability unavailable",
            ),
            audit=audit,
            outbound_network_blocked=True,
        )
