"""Integration tests for /students — registration, login, session, profile, streak,
energy, nudge. Hits real Neo4j; see tests/conftest.py for fixtures/cleanup."""

import uuid

from tests.conftest import cleanup_student


def test_register_returns_token_and_id(client, driver):
    student_number = f"test-reg-{uuid.uuid4().hex[:12]}"
    resp = client.post(
        "/students/register",
        json={"full_name": "Ada Lovelace", "student_number": student_number, "academic_year": 2},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["student_id"]
    assert body["token"]
    assert body["expires_at"]

    cleanup_student(driver, body["student_id"])


def test_register_duplicate_student_number_conflicts(client, driver):
    student_number = f"test-dup-{uuid.uuid4().hex[:12]}"
    first = client.post(
        "/students/register",
        json={"full_name": "First", "student_number": student_number, "academic_year": 1},
    )
    assert first.status_code == 201
    try:
        second = client.post(
            "/students/register",
            json={"full_name": "Second", "student_number": student_number, "academic_year": 1},
        )
        assert second.status_code == 409
        assert second.json()["error"]["code"] == "CONFLICT"
    finally:
        cleanup_student(driver, first.json()["student_id"])


def test_register_rejects_empty_full_name(client):
    resp = client.post(
        "/students/register",
        json={"full_name": "", "student_number": "x", "academic_year": 1},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_register_rejects_full_name_too_long(client):
    resp = client.post(
        "/students/register",
        json={"full_name": "A" * 201, "student_number": "x", "academic_year": 1},
    )
    assert resp.status_code == 422


def test_register_rejects_academic_year_out_of_range(client):
    resp = client.post(
        "/students/register",
        json={"full_name": "ok", "student_number": "x", "academic_year": 7},
    )
    assert resp.status_code == 422


def test_login_unknown_student_number_is_404(client):
    resp = client.post("/students/login", json={"student_number": "no-such-number"})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


def test_login_rejects_empty_student_number(client):
    resp = client.post("/students/login", json={"student_number": ""})
    assert resp.status_code == 422


def test_login_success_issues_new_token(client, student):
    resp = client.post(
        "/students/login", json={"student_number": student["student_number"]}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["student_id"] == student["student_id"]
    assert body["token"] != student["token"]

    # the freshly issued token must also work
    check = client.get(
        "/students/me", headers={"Authorization": f"Bearer {body['token']}"}
    )
    assert check.status_code == 200


def test_me_requires_bearer_token(client):
    resp = client.get("/students/me")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"


def test_me_rejects_garbage_token(client):
    resp = client.get("/students/me", headers={"Authorization": "Bearer garbage-token"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"


def test_me_with_valid_token(client, student):
    resp = client.get("/students/me", headers=student["headers"])
    assert resp.status_code == 200
    body = resp.json()
    assert body["authenticated"] is True
    assert body["student_id"] == student["student_id"]


def test_get_my_profile(client, student):
    resp = client.get("/students/me/profile", headers=student["headers"])
    assert resp.status_code == 200
    body = resp.json()
    assert body["student_id"] == student["student_id"]
    assert body["full_name"] == "Test Student"
    assert body["academic_year"] == 3


def test_logout_revokes_session(client, student):
    resp = client.post("/students/logout", headers=student["headers"])
    assert resp.status_code == 204

    resp = client.get("/students/me", headers=student["headers"])
    assert resp.status_code == 401


def test_get_my_streak(client, student):
    resp = client.get("/students/me/streak", headers=student["headers"])
    assert resp.status_code == 200
    body = resp.json()
    assert body["current_streak"] == 0
    assert len(body["week_activity"]) == 7


def test_restore_streak_not_eligible(client, student):
    resp = client.post("/students/me/streak/restore", headers=student["headers"])
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "CONFLICT"


def test_get_my_energy(client, student):
    resp = client.get("/students/me/energy", headers=student["headers"])
    assert resp.status_code == 200
    assert resp.json()["energy"] == 0


def test_get_my_nudge(client, student):
    resp = client.get("/students/me/nudge", headers=student["headers"])
    assert resp.status_code == 200
    body = resp.json()
    assert body["quiz_due_count"] == 0
    assert body["flashcard_due_count"] == 0
    assert body["total_due_count"] == 0
    assert body["soonest_due"] is None


def test_register_rate_limited_after_five_per_minute(client, driver):
    created = []
    resp = None
    for i in range(6):
        resp = client.post(
            "/students/register",
            json={
                "full_name": "Rate Limit Test",
                "student_number": f"test-rate-limit-{uuid.uuid4().hex[:12]}",
                "academic_year": 1,
            },
        )
        if resp.status_code == 201:
            created.append(resp.json()["student_id"])

    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "RATE_LIMITED"

    for sid in created:
        cleanup_student(driver, sid)
