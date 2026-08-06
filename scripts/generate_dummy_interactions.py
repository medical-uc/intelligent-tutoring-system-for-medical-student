"""
Synthetic Interaction Data Generator
======================================
Populates Neo4j directly with realistic dummy student-question interaction data for
developing and testing the app (and, eventually, AKT / the personalization machine)
before real student data exists. Writes through the same functions the real API uses
(src.student_kg.enrollment.enroll_student, src.quiz.sessions.start_session/end_session,
src.quiz.attempts.record_attempt, src.flashcards.reviews.record_review) rather than
dumping JSON + a bespoke Cypher import script — so the synthetic data is immediately
queryable via the real endpoints (/quiz/history, /quiz/review/due, /flashcards/history,
/flashcards/review/due, etc.), on the exact graph shape the app reads.

Design principle: don't generate random noise. Generate data with the same LATENT
STRUCTURE real student data has, so downstream analysis has something genuine to learn:

  1. Each student has a hidden per-topic "ability" (like IRT theta)
  2. Each topic has a difficulty (derived from the real question bank — see below)
  3. P(correct) follows a 2-parameter logistic IRT model: ability vs difficulty
  4. Ability actually INCREASES slightly after correct practice (learning)
  5. Ability DECAYS slightly over time gaps (forgetting)
  6. Confidence correlates with the ability-difficulty margin, with noise
  7. Response time correlates inversely with ability and directly with difficulty
  8. Wrong answers pick a distractor (not literally random) using a
     Zipf-like preference for "plausible" wrong options
  9. A fraction of sessions are flashcard-review sessions instead of quiz sessions,
     rating recall as again/hard/good/easy instead of grading an MCQ answer

The real question bank (notebooks/mcq_output/question_bank.json) now carries real
subject > topic tags (see scripts/annotate_mcq_topics.py) — the ability-vs-difficulty
model simulates one skill per real leaf topic (topic_tag joined with " > ", same key
src/quiz/bank.py::Question.topic_path and src/quiz/attempts.py's Topic-node merge use),
restricted to topics with at least MIN_QUESTIONS_PER_TOPIC questions so each simulated
skill has enough of a real question pool to draw repeat practice from. Sparser topics
are left out of the simulation (and so never chosen), not deleted from the bank — a
student would need repeat exposure to a topic for per-topic mastery to mean anything,
and a 1-question topic can't provide that. Every question/session write still uses real
uids and real topic_tag/topic_path, so the resulting graph is fully readable by the real
app and by src/quiz/attempts.py's Topic-node merge.

Per-topic coverage is guaranteed by construction, not left to chance. Purely random
topic selection per session (the old approach) concentrates traffic on a few popular
topics and starves the rest — exactly the failure mode Slater & Baker (2018,
Behaviormetrika, "Degree of Error in Bayesian Knowledge Tracing Estimates From
Differences in Sample Sizes") measured: fitting BKT below ~25 students per skill doesn't
converge to the true parameters at all, and below MIN_ATTEMPTS_PER_TOPIC_STUDENT
attempts per student the estimates stay noisy regardless of student count. So each topic
is pre-assigned a roster of at least MIN_STUDENTS_PER_TOPIC distinct students who are
each guaranteed at least MIN_ATTEMPTS_PER_TOPIC_STUDENT attempts on it (see
build_required_assignments()) — these required sessions are generated first, per
student, before any organic/extra random-topic sessions top up the rest of a student's
session budget. This keeps every topic eligible for a future per-topic EM fit, rather
than reproducing the same sparse, uneven coverage the notebook's own diagnostics found
before (median 6 attempts/pair, only 128/184 pairs >= 5, across just 20 students total).

Usage (needs the neo4j stack up: `make neo4j-up`; run from repo root as a module, so
`src.*` imports resolve):
    .venv/bin/python -m scripts.generate_dummy_interactions
    .venv/bin/python -m scripts.generate_dummy_interactions --n-students 20 --dry-run
"""

import argparse
import logging
import math
import random
from datetime import datetime, timedelta

from dotenv import load_dotenv

from src.flashcards.reviews import record_review
from src.quiz.attempts import record_attempt
from src.quiz.bank import Question, load_question_bank
from src.quiz.sessions import end_session, start_session
from src.student_kg.driver import ensure_constraints, make_driver
from src.student_kg.enrollment import enroll_student

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("generate_dummy_interactions")

# ─── CONFIG ──────────────────────────────────────────────────────────────────

# MIN_STUDENTS_PER_TOPIC / MIN_ATTEMPTS_PER_TOPIC_STUDENT come from Slater & Baker
# (2018): fitting BKT to fewer than 25 students per skill doesn't converge to the true
# parameters at all (their ns=5/ns=10 conditions), and 25 students + >=3-6 attempts each
# is their floor for mastery-prediction estimates to be usable (avoiding both parameter
# divergence and the ~30-40% extreme/degenerate-parameter rate they saw below that).
# 250+ students/topic is their bar for trusting the parameters as characterizing the
# skill itself, not just predicting one student's mastery point — out of reach for a
# local dummy-data generator, so this targets the mastery-prediction floor, not that one.
MIN_STUDENTS_PER_TOPIC = 25
MIN_ATTEMPTS_PER_TOPIC_STUDENT = 6

N_STUDENTS = 40  # number of synthetic students to enroll
MIN_QUESTIONS_PER_TOPIC = 5  # leaf topics below this are excluded from the simulation
N_ROOT_SUBJECTS = 4  # first N subjects (by question count) get "root_weight" priority
SESSIONS_PER_STUDENT = (8, 20)  # min/max *organic* sessions per student, on top of
# whatever required-coverage sessions build_required_assignments() assigns them
INTERACTIONS_PER_SESSION = (3, 8)  # min/max questions/cards per session
SESSION_GAP_DAYS = (1, 4)  # days between sessions (for forgetting decay)
FLASHCARD_SESSION_RATE = (
    0.35  # fraction of *organic* sessions that are flashcard drills, not quizzes —
    # required-coverage sessions are always quiz sessions, since attempt-count floor is
    # a BKT/quiz-history requirement, not a flashcard one
)
START_DATE = datetime(2026, 3, 1)

LEARNING_RATE = 0.16  # ability gain per correct answer on a concept
FORGETTING_RATE = 0.006  # ability decay per day since last practice
CONFIDENCE_NOISE = 0.35  # how noisy confidence reporting is
RESPONSE_TIME_BASE = 18.0  # baseline seconds for an easy question

random.seed(42)  # reproducible synthetic data

# ─── DOMAIN SETUP ────────────────────────────────────────────────────────────


def build_concepts(bank, min_questions: int, n_root_subjects: int) -> list[dict]:
    """One "concept" (simulated skill) per real leaf topic (topic_path), restricted to
    topics with at least `min_questions` real questions — see module docstring for why.
    `is_root` marks topics belonging to the `n_root_subjects` subjects (the topic_tag[0]
    prefix) with the most total questions, used the same way the old synthetic "root
    concept" flag was: root topics get practiced disproportionately early in a student's
    history, non-root topics get phased in more over time."""
    topic_paths = [
        t for t in bank.topics() if len(bank.questions_for_topic(t)) >= min_questions
    ]
    subject_question_counts: dict[str, int] = {}
    for t in topic_paths:
        subject = t.split(" > ")[0]
        subject_question_counts[subject] = subject_question_counts.get(
            subject, 0
        ) + len(bank.questions_for_topic(t))
    root_subjects = {
        s
        for s, _ in sorted(
            subject_question_counts.items(), key=lambda kv: kv[1], reverse=True
        )[:n_root_subjects]
    }
    return [
        {
            "topic_path": t,
            "irt_difficulty": random.gauss(0, 0.7),
            "is_root": t.split(" > ")[0] in root_subjects,
        }
        for t in topic_paths
    ]


def assign_questions_to_concepts(
    bank, concepts: list[dict]
) -> dict[str, list[Question]]:
    """Maps each simulated concept to its real question pool for that topic_path."""
    return {c["topic_path"]: bank.questions_for_topic(c["topic_path"]) for c in concepts}


def build_required_assignments(
    concepts: list[dict],
    student_ids: list[str],
    min_students_per_topic: int = MIN_STUDENTS_PER_TOPIC,
    min_attempts_per_topic_student: int = MIN_ATTEMPTS_PER_TOPIC_STUDENT,
) -> dict[str, list[tuple[str, int]]]:
    """For each student, the list of (topic_path, n_required_attempts) they must be given
    a dedicated quiz session for, before any organic/random-topic sessions run — this is
    what guarantees every topic clears MIN_STUDENTS_PER_TOPIC distinct students and
    MIN_ATTEMPTS_PER_TOPIC_STUDENT attempts each by construction (see module docstring).

    Each topic gets a shuffled roster of min(min_students_per_topic, len(student_ids))
    students — if there are fewer synthetic students than the target, every student is
    assigned instead, which is a visible under-coverage signal (the run's final summary
    reports it) rather than a silent shortfall. Rosters are built with a round-robin
    offset per topic (not independently re-shuffled) so required load spreads evenly
    across students instead of piling onto whichever students happen to shuffle first."""
    roster_size = min(min_students_per_topic, len(student_ids))
    assignments: dict[str, list[tuple[str, int]]] = {sid: [] for sid in student_ids}
    n_students = len(student_ids)
    for i, concept in enumerate(concepts):
        offset = (i * roster_size) % n_students
        roster = [student_ids[(offset + j) % n_students] for j in range(roster_size)]
        for sid in roster:
            assignments[sid].append((concept["topic_path"], min_attempts_per_topic_student))
    return assignments


# ─── STUDENT MODEL ────────────────────────────────────────────────────────────


def init_student_model(concepts: list[dict]) -> dict:
    """
    Each student gets:
      - a base learning aptitude (some students learn faster than others)
      - a per-concept starting ability (some concepts start weaker/stronger)
      - a last_practiced timestamp per concept (for forgetting decay)
    """
    base_aptitude = random.gauss(0, 0.4)
    ability = {}
    for c in concepts:
        prior = 0.2 if c["is_root"] else -0.3
        ability[c["topic_path"]] = random.gauss(prior, 0.6)
    return {
        "base_aptitude": base_aptitude,
        "ability": ability,
        "last_practiced": {},
    }


def apply_forgetting(model: dict, topic_path: str, now: datetime) -> None:
    """Decay ability toward 0 based on days since last practice."""
    last = model["last_practiced"].get(topic_path)
    if last is None:
        return
    days_gap = (now - last).total_seconds() / 86400
    decay = FORGETTING_RATE * days_gap
    model["ability"][topic_path] = model["ability"][topic_path] * math.exp(
        -decay * 0.3
    ) - decay * 0.5 * (1 if model["ability"][topic_path] > 0 else 0)


def irt_probability(ability: float, difficulty: float) -> float:
    """2-parameter logistic IRT: P(correct) = sigmoid(ability - difficulty)."""
    z = ability - difficulty
    return 1.0 / (1.0 + math.exp(-z))


def simulate_quiz_answer(
    model: dict, question: Question, topic_path: str, difficulty: float, now: datetime
) -> dict:
    """Simulate one quiz answer: correctness, selected option, confidence, response time.
    Updates the student's ability in place (learning effect)."""
    apply_forgetting(model, topic_path, now)

    ability = model["ability"][topic_path] + model["base_aptitude"] * 0.3
    p_correct = irt_probability(ability, difficulty)
    correct = random.random() < p_correct

    if correct:
        model["ability"][topic_path] += LEARNING_RATE * (1.2 - p_correct)
    else:
        model["ability"][topic_path] -= LEARNING_RATE * 0.4 * p_correct
    model["last_practiced"][topic_path] = now

    n_opts = len(question.options)
    correct_index = question.correct_option_index()
    if correct:
        selected_index = correct_index
    else:
        other_indices = [i for i in range(n_opts) if i != correct_index]
        weights = [1.0 / (rank + 1) for rank in range(len(other_indices))]
        selected_index = random.choices(other_indices, weights=weights, k=1)[0]

    margin = ability - difficulty
    conf_signal = margin + random.gauss(0, CONFIDENCE_NOISE)
    if conf_signal > 0.6:
        confidence = "confident"
    elif conf_signal > -0.3:
        confidence = "unsure"
    else:
        confidence = "guessing"
    if random.random() < 0.08:
        confidence = random.choice(["guessing", "unsure", "confident"])

    difficulty_factor = 1.0 + max(0, difficulty) * 0.6
    ability_factor = max(0.4, 1.0 - ability * 0.3)
    noise = random.gauss(1.0, 0.25)
    time_taken = max(
        3.0, RESPONSE_TIME_BASE * difficulty_factor * ability_factor * noise
    )

    return {
        "selected_index": selected_index,
        "correct": correct,
        "confidence": confidence,
        "time_taken_seconds": round(time_taken, 2),
    }


def simulate_flashcard_rating(
    model: dict, topic_path: str, difficulty: float, now: datetime
) -> str:
    """Simulate one flashcard self-rating from the same ability/difficulty margin used
    for quiz confidence — a student who'd answer confidently-correct on a concept also
    tends to recall its flashcards as "good"/"easy", and vice versa. Does NOT apply the
    quiz learning-effect update (flashcard recall practice is a separate, lighter-weight
    signal here), but DOES apply forgetting decay and updates last_practiced, since
    flashcard drilling is still practice that should stave off decay."""
    apply_forgetting(model, topic_path, now)
    ability = model["ability"][topic_path] + model["base_aptitude"] * 0.3
    model["last_practiced"][topic_path] = now

    margin = ability - difficulty + random.gauss(0, CONFIDENCE_NOISE)
    if margin > 0.7:
        return "easy"
    if margin > 0.1:
        return "good"
    if margin > -0.5:
        return "hard"
    return "again"


# ─── SESSION / SEQUENCE GENERATION ────────────────────────────────────────────


def _run_quiz_session(
    driver,
    student_id: str,
    model: dict,
    session_topic: str,
    difficulty: float,
    candidates: list[Question],
    n_in_session: int,
    current_time: datetime,
) -> tuple[datetime, int]:
    """Writes one full quiz session (start -> n_in_session graded attempts -> end) for
    session_topic, advancing and returning current_time. Shared by both the required
    (coverage-guarantee) and organic session-generation passes so a session looks
    identical either way from the graph's perspective."""
    session_id = start_session(
        driver, student_id, topic_path=session_topic, ts=current_time
    )
    n_quiz_answers = 0
    for _ in range(n_in_session):
        question = random.choice(candidates)
        current_time += timedelta(minutes=random.uniform(0.5, 4))
        result = simulate_quiz_answer(
            model, question, session_topic, difficulty, current_time
        )
        record_attempt(
            driver,
            student_id=student_id,
            session_id=session_id,
            question_uid=question.uid,
            selected_index=result["selected_index"],
            correct=result["correct"],
            confidence=result["confidence"],
            time_taken_seconds=result["time_taken_seconds"],
            topic_tag=question.topic_tag,
            ts=current_time,
        )
        n_quiz_answers += 1
    end_session(driver, student_id, session_id, ts=current_time)
    return current_time, n_quiz_answers


def generate_and_write_student(
    driver,
    student_id: str,
    concepts: list[dict],
    concept_questions: dict[str, list[Question]],
    required_topics: list[tuple[str, int]] | None = None,
) -> dict:
    """Generates and writes one student's full multi-session interaction history straight
    to Neo4j via the real quiz/flashcard write functions, backdated across sessions/days.
    Returns a small summary dict for the run's final report.

    required_topics (from build_required_assignments()) is a list of (topic_path,
    n_required_attempts) this student must get a dedicated quiz session for, run BEFORE
    the organic session loop below — this is what guarantees per-topic coverage rather
    than leaving it to chance (see module docstring). Each required topic becomes exactly
    one session with n_required_attempts questions in it, so a topic's
    MIN_ATTEMPTS_PER_TOPIC_STUDENT floor is met even if that student never happens to
    land on the topic again organically."""
    model = init_student_model(concepts)
    n_organic_sessions = random.randint(*SESSIONS_PER_STUDENT)
    current_time = START_DATE + timedelta(days=random.randint(0, 10))

    root_topics = [c["topic_path"] for c in concepts if c["is_root"]]
    other_topics = [c["topic_path"] for c in concepts if not c["is_root"]]
    difficulty_by_topic = {c["topic_path"]: c["irt_difficulty"] for c in concepts}

    n_quiz_answers = 0
    n_flashcard_reviews = 0
    n_required_sessions = 0

    for topic_path, n_required in required_topics or []:
        candidates = concept_questions.get(topic_path) or []
        if not candidates:
            continue
        current_time, written = _run_quiz_session(
            driver,
            student_id,
            model,
            topic_path,
            difficulty_by_topic[topic_path],
            candidates,
            n_required,
            current_time,
        )
        n_quiz_answers += written
        n_required_sessions += 1
        current_time += timedelta(days=random.uniform(*SESSION_GAP_DAYS))

    for session_idx in range(n_organic_sessions):
        n_in_session = random.randint(*INTERACTIONS_PER_SESSION)
        progress = session_idx / max(1, n_organic_sessions - 1)
        root_weight = max(0.15, 0.75 - progress * 0.6)
        is_flashcard_session = random.random() < FLASHCARD_SESSION_RATE

        # A real quiz run is scoped to one topic (see app/routers/quiz.py's
        # /topics/{path}/sessions) — pick the session's topic once, up front, rather than
        # per-question, so the synthetic sessions match that shape.
        if random.random() < root_weight and root_topics:
            session_topic = random.choice(root_topics)
        else:
            session_topic = random.choice(other_topics if other_topics else root_topics)
        candidates = concept_questions.get(session_topic) or []
        if not candidates:
            current_time += timedelta(days=random.uniform(*SESSION_GAP_DAYS))
            continue
        difficulty = difficulty_by_topic[session_topic]

        if is_flashcard_session:
            for _ in range(n_in_session):
                question = random.choice(candidates)
                current_time += timedelta(minutes=random.uniform(0.5, 4))
                rating = simulate_flashcard_rating(
                    model, session_topic, difficulty, current_time
                )
                record_review(
                    driver,
                    student_id,
                    question_uid=question.uid,
                    rating=rating,
                    ts=current_time,
                )
                n_flashcard_reviews += 1
        else:
            current_time, written = _run_quiz_session(
                driver,
                student_id,
                model,
                session_topic,
                difficulty,
                candidates,
                n_in_session,
                current_time,
            )
            n_quiz_answers += written

        current_time += timedelta(days=random.uniform(*SESSION_GAP_DAYS))

    return {
        "n_sessions": n_required_sessions + n_organic_sessions,
        "n_quiz_answers": n_quiz_answers,
        "n_flashcard_reviews": n_flashcard_reviews,
    }


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--n-students", type=int, default=N_STUDENTS)
    ap.add_argument(
        "--min-questions-per-topic", type=int, default=MIN_QUESTIONS_PER_TOPIC
    )
    ap.add_argument(
        "--min-students-per-topic",
        type=int,
        default=MIN_STUDENTS_PER_TOPIC,
        help="Slater & Baker (2018) floor for BKT parameter estimates to converge at all",
    )
    ap.add_argument(
        "--min-attempts-per-topic-student",
        type=int,
        default=MIN_ATTEMPTS_PER_TOPIC_STUDENT,
    )
    ap.add_argument(
        "--dry-run", action="store_true", help="print the plan without writing to Neo4j"
    )
    args = ap.parse_args()

    bank = load_question_bank()
    concepts = build_concepts(bank, args.min_questions_per_topic, N_ROOT_SUBJECTS)
    concept_questions = assign_questions_to_concepts(bank, concepts)
    n_questions_in_sim = sum(len(qs) for qs in concept_questions.values())
    log.info(
        "loaded %d topics (>= %d questions each), %d questions in the simulation pool",
        len(concepts),
        args.min_questions_per_topic,
        n_questions_in_sim,
    )

    if args.n_students < args.min_students_per_topic:
        log.warning(
            "--n-students (%d) < --min-students-per-topic (%d): every topic will be "
            "capped at %d students, below the Slater & Baker convergence floor",
            args.n_students,
            args.min_students_per_topic,
            args.n_students,
        )
    required_attempts_per_student = (
        len(concepts) * args.min_students_per_topic * args.min_attempts_per_topic_student
    ) / max(1, args.n_students)
    log.info(
        "coverage target: >= %d students x >= %d attempts per topic "
        "(~%.0f required attempts/student on average before organic sessions)",
        args.min_students_per_topic,
        args.min_attempts_per_topic_student,
        required_attempts_per_student,
    )

    if args.dry_run:
        log.info(
            "dry run: would enroll %d students across %d simulated topics, %d questions bucketed",
            args.n_students,
            len(concepts),
            n_questions_in_sim,
        )
        return

    load_dotenv()
    driver = make_driver()
    ensure_constraints(driver)
    try:
        student_ids = []
        for i in range(args.n_students):
            student_id = enroll_student(
                driver,
                full_name=f"Synthetic Student {i:04d}",
                student_number=f"SYNTH-{i:04d}",
                academic_year=random.randint(1, 6),
            )
            student_ids.append(student_id)

        required_assignments = build_required_assignments(
            concepts,
            student_ids,
            min_students_per_topic=args.min_students_per_topic,
            min_attempts_per_topic_student=args.min_attempts_per_topic_student,
        )

        totals = {"n_sessions": 0, "n_quiz_answers": 0, "n_flashcard_reviews": 0}
        for i, student_id in enumerate(student_ids):
            summary = generate_and_write_student(
                driver,
                student_id,
                concepts,
                concept_questions,
                required_topics=required_assignments[student_id],
            )
            for k in totals:
                totals[k] += summary[k]
            log.info(
                "student %s (%s): %d sessions, %d quiz answers, %d flashcard reviews",
                student_id,
                f"SYNTH-{i:04d}",
                summary["n_sessions"],
                summary["n_quiz_answers"],
                summary["n_flashcard_reviews"],
            )

        log.info(
            "done: %d students, %d sessions, %d quiz answers, %d flashcard reviews written to Neo4j",
            args.n_students,
            totals["n_sessions"],
            totals["n_quiz_answers"],
            totals["n_flashcard_reviews"],
        )
    finally:
        driver.close()


if __name__ == "__main__":
    main()
