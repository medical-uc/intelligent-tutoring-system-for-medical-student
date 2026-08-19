"""Cross-cutting checks on the error envelope itself: every error response — no
matter which handler produced it — shares {"error": {"code", "message", [details]}},
and nothing internal (Cypher text, stack traces, driver messages) ever leaks to the
client. Exercised via a handful of representative endpoints rather than every one;
per-endpoint status codes are covered in test_students_api.py / test_quiz_api.py /
test_flashcards_api.py."""

import pytest


def _assert_envelope(body: dict, expected_code: str) -> None:
    assert "error" in body
    assert set(body["error"]).issubset({"code", "message", "details"})
    assert body["error"]["code"] == expected_code
    assert isinstance(body["error"]["message"], str)
    assert body["error"]["message"]


@pytest.mark.parametrize(
    "method,path,kwargs,expected_status,expected_code",
    [
        ("GET", "/quiz/mastery", {}, 401, "UNAUTHORIZED"),
        ("POST", "/students/login", {"json": {"student_number": "nope-xyz"}}, 404, "NOT_FOUND"),
        (
            "POST",
            "/students/register",
            {"json": {"full_name": "", "student_number": "x", "academic_year": 1}},
            422,
            "VALIDATION_ERROR",
        ),
        ("GET", "/subjects/NO-SUCH-TOPIC/questions", {}, 404, "NOT_FOUND"),
        ("GET", "/does-not-exist", {}, 401, "UNAUTHORIZED"),
    ],
)
def test_error_envelope_shape(client, method, path, kwargs, expected_status, expected_code):
    resp = client.request(method, path, **kwargs)
    assert resp.status_code == expected_status
    _assert_envelope(resp.json(), expected_code)


def test_validation_error_includes_field_details(client):
    resp = client.post(
        "/students/register",
        json={"full_name": "", "student_number": "x", "academic_year": 1},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert "details" in body["error"]
    assert any(d["loc"] == ["body", "full_name"] for d in body["error"]["details"])


def test_non_validation_errors_have_no_details_key(client):
    resp = client.post("/students/login", json={"student_number": "nope-xyz"})
    assert resp.status_code == 404
    assert "details" not in resp.json()["error"]


def test_malformed_json_body_is_422_not_500(client):
    resp = client.post(
        "/students/register",
        content="not valid json{{{",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 422
    _assert_envelope(resp.json(), "VALIDATION_ERROR")


def test_missing_bearer_scheme_is_401(client):
    resp = client.get("/quiz/mastery", headers={"Authorization": "garbage-no-scheme"})
    assert resp.status_code == 401
    _assert_envelope(resp.json(), "UNAUTHORIZED")


def test_wrong_auth_scheme_is_401(client, student):
    resp = client.get(
        "/quiz/mastery",
        headers={"Authorization": f"Basic {student['token']}"},
    )
    assert resp.status_code == 401


def test_response_never_leaks_internal_error_text(client, student):
    """Deliberately trigger a downstream lookup failure and confirm the response body
    never echoes raw Cypher, driver class names, or a traceback — only the safe,
    generic envelope message."""
    resp = client.post(
        "/quiz/sessions/definitely-not-a-real-session-id/end",
        headers=student["headers"],
    )
    assert resp.status_code == 404
    text = resp.text.lower()
    for leak in ("traceback", "neo4j", "cypher", "site-packages", "  file \""):
        assert leak not in text


def test_duplicate_registration_conflict_does_not_leak_constraint_text(client, driver):
    import uuid

    from tests.conftest import cleanup_student

    student_number = f"test-leak-check-{uuid.uuid4().hex[:12]}"
    first = client.post(
        "/students/register",
        json={"full_name": "First", "student_number": student_number, "academic_year": 1},
    )
    try:
        second = client.post(
            "/students/register",
            json={"full_name": "Second", "student_number": student_number, "academic_year": 1},
        )
        assert second.status_code == 409
        body = second.json()
        _assert_envelope(body, "CONFLICT")
        assert "neo4j" not in body["error"]["message"].lower()
        assert "constraint" not in body["error"]["message"].lower() or (
            "already exists" in body["error"]["message"].lower()
        )
    finally:
        cleanup_student(driver, first.json()["student_id"])
