"""Namuna rasmlar (/training): teaching samples per equipment category.

The point of the feature: the generic COCO model calls a white mouse in a hand a
"smartphone", so real frames have to be collected for a retrain. These tests
cover storage, de-duplication, the admin-only gate and the camera capture path.
"""

from __future__ import annotations

import re
import zipfile
from io import BytesIO

import cv2
import numpy as np
import pytest

from app.api import training as training_api
from app.services import sample_service

CATEGORY = "mouse"


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "CSRF token not found in page"
    return match.group(1)


def login(client, username: str = "admin", password: str = "Admin123!") -> None:
    token = csrf_from(client.get("/login").text)
    response = client.post(
        "/login",
        data={"username": username, "password": password, "next": "/", "csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text


def _jpeg(seed: int) -> bytes:
    """A small valid JPEG whose bytes differ per *seed*."""
    image = np.full((32, 32, 3), seed % 255, dtype=np.uint8)
    ok, buffer = cv2.imencode(".jpg", image)
    assert ok
    return buffer.tobytes()


@pytest.fixture(autouse=True)
def clean_samples(settings):
    """The temp data directory is shared by the session, so start empty."""
    sample_service.clear_category(settings, CATEGORY)
    yield
    sample_service.clear_category(settings, CATEGORY)


@pytest.fixture()
def admin(client):
    """The whole section is admin-only, so sign in once per test."""
    login(client)
    return client


def _upload(client, code: str = CATEGORY, files: list[tuple[str, bytes, str]] | None = None):
    token = csrf_from(client.get("/training").text)
    payload = files or [(f"maus_{index}.jpg", _jpeg(index + 1), "image/jpeg") for index in range(2)]
    return client.post(
        f"/api/training/{code}/samples",
        data={"csrf_token": token},
        files=[("files", item) for item in payload],
        follow_redirects=False,
    )


# -- access --------------------------------------------------------------------
def test_training_page_is_admin_only(admin, other_user_factory):
    client = admin
    page = client.get("/training")
    assert page.status_code == 200
    assert "Namuna rasmlar" in page.text

    other_user_factory("op", "operator")
    client.post("/logout", data={})
    login(client, "op", "Operator123!")
    assert client.get("/training").status_code == 403


def test_sample_image_requires_admin(admin, other_user_factory):
    client = admin
    _upload(client)
    samples = sample_service.list_samples(None, CATEGORY)
    assert samples, "the upload should have stored at least one file"
    url = f"/api/training/{CATEGORY}/samples/{samples[0].name}"
    assert client.get(url).status_code == 200

    other_user_factory("op", "operator")
    client.post("/logout", data={})
    login(client, "op", "Operator123!")
    assert client.get(url).status_code == 403


# -- upload --------------------------------------------------------------------
def test_upload_stores_samples_and_lists_them(admin, settings):
    client = admin
    response = _upload(client)

    assert response.status_code == 303
    stored = sample_service.list_samples(settings, CATEGORY)
    assert len(stored) == 2
    assert all(path.name.startswith(f"{CATEGORY}_") for path in stored)

    page = client.get(f"/training?code={CATEGORY}").text
    assert stored[0].name in page


def test_identical_pictures_are_not_stored_twice(admin, settings):
    client = admin
    same = [("a.jpg", _jpeg(7), "image/jpeg"), ("b.jpg", _jpeg(7), "image/jpeg")]

    _upload(client, files=same)

    assert len(sample_service.list_samples(settings, CATEGORY)) == 1


def test_non_image_upload_is_refused_with_a_message(admin, settings):
    client = admin
    response = client.post(
        f"/api/training/{CATEGORY}/samples",
        data={"csrf_token": csrf_from(client.get("/training").text)},
        files=[("files", ("notes.txt", b"salom", "text/plain"))],
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Faqat JPG, PNG yoki WEBP" in response.text
    assert sample_service.list_samples(settings, CATEGORY) == []


def test_unknown_category_is_refused(admin, settings):
    client = admin
    response = _upload(client, code="nosuchcategory")

    assert response.status_code == 303
    assert sample_service.list_samples(settings, "nosuchcategory") == []


# -- delete / zip ---------------------------------------------------------------
def test_delete_removes_the_file(admin, settings):
    client = admin
    _upload(client)
    victim = sample_service.list_samples(settings, CATEGORY)[0]
    token = csrf_from(client.get("/training").text)

    response = client.post(
        f"/api/training/{CATEGORY}/samples/{victim.name}/delete",
        data={"csrf_token": token},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert not victim.exists()
    assert len(sample_service.list_samples(settings, CATEGORY)) == 1


def test_zip_contains_the_samples(admin, settings):
    client = admin
    _upload(client)

    response = client.get(f"/api/training/samples.zip?code={CATEGORY}")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        names = archive.namelist()
    assert len(names) == 2
    assert all(name.startswith(f"{CATEGORY}/") for name in names)


# -- camera capture -------------------------------------------------------------
class _Worker:
    def __init__(self, frame):
        self._frame = frame

    def latest_frame(self):
        return self._frame.copy()


class _Monitor:
    def __init__(self, worker):
        self._worker = worker

    def get_worker(self, code: str):
        return self._worker


def test_capture_without_camera_explains_the_problem(admin, monkeypatch, settings):
    client = admin
    monkeypatch.setattr(training_api, "get_monitor", lambda: _Monitor(None))

    response = client.post(
        f"/api/training/{CATEGORY}/capture",
        data={"csrf_token": csrf_from(client.get("/training").text), "camera": "CAM"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Kameradan kadr olinmadi" in response.text
    assert sample_service.list_samples(settings, CATEGORY) == []


def test_capture_stores_the_live_frame(admin, monkeypatch, settings):
    client = admin
    frame = np.full((120, 160, 3), 90, dtype=np.uint8)
    monkeypatch.setattr(training_api, "get_monitor", lambda: _Monitor(_Worker(frame)))

    response = client.post(
        f"/api/training/{CATEGORY}/capture",
        data={"csrf_token": csrf_from(client.get("/training").text), "camera": "CAM"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    stored = sample_service.list_samples(settings, CATEGORY)
    assert len(stored) == 1
    assert "Kadr saqlandi" in response.text


# -- progress --------------------------------------------------------------------
def test_progress_marks_a_category_ready_at_ten(admin, settings):
    client = admin
    uploads = [(f"m{index}.jpg", _jpeg(index + 20)) for index in range(10)]
    sample_service.add_images(settings, CATEGORY, uploads)

    # progress() needs a session; the page does the same work, so assert through HTML.
    page = client.get(f"/training?code={CATEGORY}").text

    assert "o‘qitishga tayyor" in page or "minimal yetdi" in page
    assert len(sample_service.list_samples(settings, CATEGORY)) == 10
