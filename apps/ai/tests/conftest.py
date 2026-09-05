"""Test-only process configuration for the strict local service settings."""

import os

os.environ.setdefault(
    "WORKBENCH_APP_AUTH_SIGNING_SECRET",
    "test-only-secret-material-32-bytes-minimum",
)
