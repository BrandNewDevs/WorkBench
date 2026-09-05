"""Frozen FastAPI service entry point for the packaged desktop application."""

import uvicorn

from app.config import ApplicationSettings
from app.main import create_app


def main() -> None:
    """Run the service using the packaged process environment."""

    settings = ApplicationSettings()
    uvicorn.run(create_app(settings=settings), host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    main()
