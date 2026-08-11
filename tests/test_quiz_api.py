"""Integration tests for /quiz — topics/subjects, sessions, check/log, mastery, review
queue. Hits real Neo4j + Postgres-backed question bank; see tests/conftest.py."""


def test_list_topics_requires_no_auth(client):
    resp = client.get("/quiz/topics")
    assert resp.status_code == 200
    assert isinstance(resp.json()["topics"], list)
    assert len(resp.json()["topics"]) > 0


def test_list_subjects(client):
    resp = client.get("/quiz/subjects")
    assert resp.status_code == 200
    subjects = resp.json()["subjects"]
    assert len(subjects) > 0
    assert "name" in subjects[0]
    assert "topics" in subjects[0]


def test_get_questions_for_unknown_topic_is_404(client):
    resp = client.get("/quiz/topics/NO-SUCH-TOPIC-XYZ/questions")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


def test_get_questions_for_real_topic(client, sample_topic):
    resp = client.get(f"/quiz/topics/{sample_topic}/questions")
    assert resp.status_code == 200
    questions = resp.json()
    assert len(questions) > 0
    q = questions[0]
    assert "uid" in q
    assert "options" in q
    # answer key must never leak to the client
    assert all("correct" not in opt for opt in q["options"])


def test_check_answer_no_auth_required(client, sample_question):
    correct_index = sample_question.correct_option_index()
    resp = client.post(
        f"/quiz/questions/{sample_question.uid}/check",
        json={"selected_index": correct_index},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["correct"] is True
    assert body["correct_index"] == correct_index


def test_check_answer_unknown_question_is_404(client):
    resp = client.post(
        "/quiz/questions/no-such-uid/check", json={"selected_index": 0}
    )
    assert resp.status_code == 404


def test_check_answer_out_of_range_index_is_400(client, sample_question):
    # within the schema's ge/le bounds (0-25) but beyond this question's own option
    # count — Pydantic lets it through, the router's manual range check must catch it
    out_of_range = len(sample_question.options) + 10
    assert out_of_range <= 25, "sample_question has too many options for this bound"
    resp = client.post(
        f"/quiz/questions/{sample_question.uid}/check",
        json={"selected_index": out_of_range},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "BAD_REQUEST"


def test_check_answer_rejects_negative_index(client, sample_question):
    resp = client.post(
        f"/quiz/questions/{sample_question.uid}/check",
        json={"selected_index": -1},
    )
    assert resp.status_code == 422


def test_start_session_requires_auth(client, sample_topic):
    resp = client.post(f"/quiz/topics/{sample_topic}/sessions")
    assert resp.status_code == 401


def test_start_session_unknown_topic_is_404(client, student):
    resp = client.post(
        "/quiz/topics/NO-SUCH-TOPIC-XYZ/sessions", headers=student["headers"]
    )
    assert resp.status_code == 404


def test_full_quiz_session_flow(client, student, sample_topic, sample_question):
    start = client.post(
        f"/quiz/topics/{sample_topic}/sessions", headers=student["headers"]
    )
    assert start.status_code == 200
    session_id = start.json()["session_id"]

    correct_index = sample_question.correct_option_index()
    log = client.post(
        f"/quiz/questions/{sample_question.uid}/log",
        headers=student["headers"],
        json={
            "session_id": session_id,
            "selected_index": correct_index,
            "confidence": "confident",
            "time_taken_seconds": 12.5,
        },
    )
    assert log.status_code == 200
    log_body = log.json()
    assert log_body["correct"] is True
    assert log_body["event_id"]

    end = client.post(
        f"/quiz/sessions/{session_id}/end", headers=student["headers"]
    )
    assert end.status_code == 200
    end_body = end.json()
    assert end_body["question_count"] == 1
    assert end_body["correct_count"] == 1


def test_log_attempt_unknown_session_is_404(client, student, sample_question):
    resp = client.post(
        f"/quiz/questions/{sample_question.uid}/log",
        headers=student["headers"],
        json={
            "session_id": "no-such-session",
            "selected_index": 0,
            "confidence": "guessing",
            "time_taken_seconds": 1,
        },
    )
    assert resp.status_code == 404


def test_log_attempt_rejects_invalid_confidence(client, student, sample_question):
    resp = client.post(
        f"/quiz/questions/{sample_question.uid}/log",
        headers=student["headers"],
        json={
            "session_id": "whatever",
            "selected_index": 0,
            "confidence": "super-sure",
            "time_taken_seconds": 1,
        },
    )
    assert resp.status_code == 422


def test_log_attempt_rejects_negative_time_taken(client, student, sample_question):
    resp = client.post(
        f"/quiz/questions/{sample_question.uid}/log",
        headers=student["headers"],
        json={
            "session_id": "whatever",
            "selected_index": 0,
            "confidence": "guessing",
            "time_taken_seconds": -5,
        },
    )
    assert resp.status_code == 422


def test_cancel_session(client, student, sample_topic):
    start = client.post(
        f"/quiz/topics/{sample_topic}/sessions", headers=student["headers"]
    )
    session_id = start.json()["session_id"]

    cancel = client.post(
        f"/quiz/sessions/{session_id}/cancel", headers=student["headers"]
    )
    assert cancel.status_code == 200
    assert cancel.json()["session_id"] == session_id


def test_cancel_unknown_session_is_404(client, student):
    resp = client.post(
        "/quiz/sessions/no-such-session/cancel", headers=student["headers"]
    )
    assert resp.status_code == 404


def test_end_session_belonging_to_other_student_is_404(
    client, student, register_student, sample_topic
):
    other = register_student()
    start = client.post(
        f"/quiz/topics/{sample_topic}/sessions", headers=other["headers"]
    )
    session_id = start.json()["session_id"]

    resp = client.post(
        f"/quiz/sessions/{session_id}/end", headers=student["headers"]
    )
    assert resp.status_code == 404


def test_get_history_empty_for_new_student(client, student):
    resp = client.get("/quiz/history", headers=student["headers"])
    assert resp.status_code == 200
    assert resp.json()["items"] == []


def test_get_review_due_empty_for_new_student(client, student):
    resp = client.get("/quiz/review/due", headers=student["headers"])
    assert resp.status_code == 200
    assert resp.json()["items"] == []


def test_get_mastery_empty_for_new_student(client, student):
    resp = client.get("/quiz/mastery", headers=student["headers"])
    assert resp.status_code == 200
    assert resp.json()["items"] == []


def test_update_mastery(client, student, sample_topic):
    resp = client.put(
        "/quiz/mastery",
        headers=student["headers"],
        json={"items": [{"topic_path": sample_topic, "p_know": 0.75}]},
    )
    assert resp.status_code == 200
    assert resp.json()["updated_count"] == 1

    fetched = client.get("/quiz/mastery", headers=student["headers"])
    items = fetched.json()["items"]
    assert len(items) == 1
    assert items[0]["p_know"] == 0.75


def test_update_mastery_rejects_p_know_out_of_range(client, student, sample_topic):
    resp = client.put(
        "/quiz/mastery",
        headers=student["headers"],
        json={"items": [{"topic_path": sample_topic, "p_know": 1.5}]},
    )
    assert resp.status_code == 422


def test_update_mastery_rejects_empty_items(client, student):
    resp = client.put(
        "/quiz/mastery", headers=student["headers"], json={"items": []}
    )
    assert resp.status_code == 422


def test_quiz_endpoints_require_auth(client, sample_topic):
    for method, path in [
        ("GET", "/quiz/history"),
        ("GET", "/quiz/review/due"),
        ("GET", "/quiz/mastery"),
        ("PUT", "/quiz/mastery"),
    ]:
        resp = client.request(method, path, json={"items": []} if method == "PUT" else None)
        assert resp.status_code == 401, f"{method} {path} should require auth"
