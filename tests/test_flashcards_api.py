"""Integration tests for /flashcards — card browsing, sessions, reveal/log, review
queue, history. Hits real Neo4j + Postgres-backed question bank; see
tests/conftest.py. Flashcards are 1:1-derived from quiz bank questions, so
sample_question.uid doubles as a valid flashcard uid."""


def test_get_all_cards_no_auth_required(client):
    resp = client.get("/flashcards/cards")
    assert resp.status_code == 200
    cards = resp.json()
    assert len(cards) > 0
    assert "back" not in cards[0]  # answer withheld until /reveal


def test_get_cards_for_unknown_topic_is_404(client):
    resp = client.get("/flashcards/topics/NO-SUCH-TOPIC-XYZ/cards")
    assert resp.status_code == 404


def test_get_cards_for_real_topic(client, sample_topic):
    resp = client.get(f"/flashcards/topics/{sample_topic}/cards")
    assert resp.status_code == 200
    assert len(resp.json()) > 0


def test_reveal_card_no_auth_required(client, sample_question):
    resp = client.post(f"/flashcards/cards/{sample_question.uid}/reveal")
    assert resp.status_code == 200
    body = resp.json()
    assert body["uid"] == sample_question.uid
    assert body["back"]


def test_reveal_unknown_card_is_404(client):
    resp = client.post("/flashcards/cards/no-such-uid/reveal")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


def test_start_session_requires_auth(client):
    resp = client.post("/flashcards/sessions")
    assert resp.status_code == 401


def test_start_session_default_size(client, student):
    resp = client.post("/flashcards/sessions", headers=student["headers"])
    assert resp.status_code == 200
    body = resp.json()
    assert 1 <= len(body["card_uids"]) <= 10


def test_start_session_custom_size(client, student):
    resp = client.post(
        "/flashcards/sessions", headers=student["headers"], json={"size": 3}
    )
    assert resp.status_code == 200
    assert len(resp.json()["card_uids"]) <= 3


def test_start_session_rejects_size_over_max(client, student):
    resp = client.post(
        "/flashcards/sessions", headers=student["headers"], json={"size": 51}
    )
    assert resp.status_code == 422


def test_start_session_rejects_size_zero(client, student):
    resp = client.post(
        "/flashcards/sessions", headers=student["headers"], json={"size": 0}
    )
    assert resp.status_code == 422


def test_start_session_for_unknown_topic_is_404(client, student):
    resp = client.post(
        "/flashcards/topics/NO-SUCH-TOPIC-XYZ/sessions", headers=student["headers"]
    )
    assert resp.status_code == 404


def test_full_flashcard_session_flow(client, student, sample_question):
    start = client.post(
        "/flashcards/sessions", headers=student["headers"], json={"size": 5}
    )
    assert start.status_code == 200
    session_id = start.json()["session_id"]

    log = client.post(
        f"/flashcards/cards/{sample_question.uid}/log",
        headers=student["headers"],
        json={"session_id": session_id, "rating": "good"},
    )
    assert log.status_code == 200
    log_body = log.json()
    assert log_body["event_id"]
    assert log_body["streak"] >= 1

    end = client.post(
        f"/flashcards/sessions/{session_id}/end", headers=student["headers"]
    )
    assert end.status_code == 200
    assert end.json()["reviewed_count"] == 1


def test_log_review_unknown_card_is_404(client, student):
    resp = client.post(
        "/flashcards/cards/no-such-uid/log",
        headers=student["headers"],
        json={"session_id": "whatever", "rating": "good"},
    )
    assert resp.status_code == 404


def test_log_review_unknown_session_is_404(client, student, sample_question):
    resp = client.post(
        f"/flashcards/cards/{sample_question.uid}/log",
        headers=student["headers"],
        json={"session_id": "no-such-session", "rating": "good"},
    )
    assert resp.status_code == 404


def test_log_review_rejects_invalid_rating(client, student, sample_question):
    resp = client.post(
        f"/flashcards/cards/{sample_question.uid}/log",
        headers=student["headers"],
        json={"session_id": "whatever", "rating": "meh"},
    )
    assert resp.status_code == 422


def test_log_review_rejects_missing_session_id(client, student, sample_question):
    resp = client.post(
        f"/flashcards/cards/{sample_question.uid}/log",
        headers=student["headers"],
        json={"rating": "good"},
    )
    assert resp.status_code == 422


def test_cancel_session(client, student):
    start = client.post("/flashcards/sessions", headers=student["headers"])
    session_id = start.json()["session_id"]

    resp = client.post(
        f"/flashcards/sessions/{session_id}/cancel", headers=student["headers"]
    )
    assert resp.status_code == 200


def test_cancel_unknown_session_is_404(client, student):
    resp = client.post(
        "/flashcards/sessions/no-such-session/cancel", headers=student["headers"]
    )
    assert resp.status_code == 404


def test_end_session_belonging_to_other_student_is_404(
    client, student, register_student
):
    other = register_student()
    start = client.post("/flashcards/sessions", headers=other["headers"])
    session_id = start.json()["session_id"]

    resp = client.post(
        f"/flashcards/sessions/{session_id}/end", headers=student["headers"]
    )
    assert resp.status_code == 404


def test_session_history_empty_for_new_student(client, student):
    resp = client.get("/flashcards/sessions/history", headers=student["headers"])
    assert resp.status_code == 200
    assert resp.json()["items"] == []


def test_review_history_empty_for_unreviewed_card(client, student, sample_question):
    resp = client.get(
        f"/flashcards/cards/{sample_question.uid}/history", headers=student["headers"]
    )
    assert resp.status_code == 200
    assert resp.json()["items"] == []


def test_flat_history_empty_for_new_student(client, student):
    resp = client.get("/flashcards/history", headers=student["headers"])
    assert resp.status_code == 200
    assert resp.json()["items"] == []


def test_due_for_review_empty_for_new_student(client, student):
    resp = client.get("/flashcards/review/due", headers=student["headers"])
    assert resp.status_code == 200
    assert resp.json()["items"] == []


def test_authenticated_endpoints_require_auth(client):
    for method, path in [
        ("POST", "/flashcards/sessions"),
        ("GET", "/flashcards/sessions/history"),
        ("GET", "/flashcards/history"),
        ("GET", "/flashcards/review/due"),
    ]:
        resp = client.request(method, path)
        assert resp.status_code == 401, f"{method} {path} should require auth"
