"""Pytest fixtures.

Everything runs against a throw-away SQLite database inside a temporary
directory, so the developer database is never touched and no camera, model or
network resource is required.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Iterator

import pytest

# The environment must be prepared *before* app.config is imported.
_TEMP_ROOT = Path(tempfile.mkdtemp(prefix="cctv-tests-"))
os.environ["APP_ENV"] = "test"
os.environ["DATA_DIR"] = str(_TEMP_ROOT / "data")
os.environ["LOG_DIR"] = str(_TEMP_ROOT / "logs")
os.environ["DATABASE_URL"] = f"sqlite:///{(_TEMP_ROOT / 'data' / 'test.db').as_posix()}"
os.environ["GOOGLE_SHEETS_ENABLED"] = "false"
os.environ["SIMULATION_MODE"] = "false"
os.environ["PROCESSING_FPS"] = "10"
os.environ["CAMERA_RECONNECT_INTERVAL"] = "1"
os.environ["CAMERA_MAX_RETRY_INTERVAL"] = "2"
os.environ["EVIDENCE_ENABLED"] = "true"
# The QR label reader must stay off in tests: it would decode frames per camera.
os.environ["ASSET_MONITOR_ENABLED"] = "false"
os.environ["BOOTSTRAP_ADMIN_USERNAME"] = "admin"
os.environ["BOOTSTRAP_ADMIN_PASSWORD"] = "Admin123!"
os.environ["APP_SECRET_KEY"] = "test-only-secret-key"

from app.config import Settings, reload_settings  # noqa: E402
from app.database import get_session_factory, reset_for_tests  # noqa: E402
from app.models import User  # noqa: E402
from app.services.bootstrap import bootstrap_database  # noqa: E402
from app.utils.security import hash_password  # noqa: E402


@pytest.fixture(scope="session")
def settings() -> Settings:
    settings = reload_settings()
    settings.ensure_directories()
    return settings


@pytest.fixture(autouse=True)
def fresh_database(settings: Settings) -> Iterator[None]:
    """Every test starts with an empty schema and the seeded reference data."""
    reset_for_tests(settings.database_url)
    bootstrap_database(settings)
    yield


@pytest.fixture()
def session() -> Iterator:
    db = get_session_factory()()
    try:
        yield db
        db.commit()
    finally:
        db.rollback()
        db.close()


@pytest.fixture()
def admin_user(session) -> User:
    return session.query(User).filter(User.username == "admin").one()


@pytest.fixture()
def other_user_factory(session):
    """Create extra users with a specific role (operator / viewer)."""

    def _create(username: str, role: str = "operator") -> User:
        user = User(
            username=username,
            full_name=username.title(),
            password_hash=hash_password("Operator123!"),
            role=role,
            is_active=True,
        )
        session.add(user)
        session.commit()
        return user

    return _create


@pytest.fixture()
def asset(session, admin_user):
    """One registered device with its QR label (shared by the QR inventory tests)."""
    from app.services import asset_service

    item = asset_service.create_asset(
        session,
        category_code="desktop_pc",
        asset_code="PC-001",
        name="Buxgalteriya kompyuteri",
        room="201-xona",
        department="Buxgalteriya",
        model="OptiPlex 7010",
        user=admin_user,
    )
    session.commit()
    return item


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    # No ``with`` block on purpose: the lifespan starts camera threads, which a
    # unit test must not do.
    return TestClient(app)
