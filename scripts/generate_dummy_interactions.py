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

  1. Each student has a hidden per-concept "ability" (like IRT theta)
  2. Each question has a difficulty (from a synthetic concept bucketing — see below)
  3. P(correct) follows a 2-parameter logistic IRT model: ability vs difficulty
  4. Ability actually INCREASES slightly after correct practice (learning)
  5. Ability DECAYS slightly over time gaps (forgetting)
  6. Confidence correlates with the ability-difficulty margin, with noise
  7. Response time correlates inversely with ability and directly with difficulty
  8. Wrong answers pick a distractor (not literally random) using a
     Zipf-like preference for "plausible" wrong options
  9. A fraction of sessions are flashcard-review sessions instead of quiz sessions,
     rating recall as again/hard/good/easy instead of grading an MCQ answer

The real question bank (notebooks/mcq_output/question_bank.json) has no per-question
topic/difficulty taxonomy yet — every question sits under a single "Master MCQ" topic
with difficulty=1 (see src/quiz/bank.py). The ability-vs-difficulty concept model needs
*some* grouping to simulate against, so questions are randomly bucketed into N synthetic
concepts purely for this simulation's math; the question content/uid used in every write
is real, so the resulting graph is fully readable by the real app.

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

N_STUDENTS = 20  # number of synthetic students to enroll
N_CONCEPTS = 25  # synthetic concept buckets to spread real questions across
SESSIONS_PER_STUDENT = (8, 20)  # min/max sessions per student
INTERACTIONS_PER_SESSION = (3, 8)  # min/max questions/cards per session
SESSION_GAP_DAYS = (1, 4)  # days between sessions (for forgetting decay)
FLASHCARD_SESSION_RATE = (
    0.35  # fraction of sessions that are flashcard drills, not quizzes
)
START_DATE = datetime(2026, 3, 1)

LEARNING_RATE = 0.16  # ability gain per correct answer on a concept
FORGETTING_RATE = 0.006  # ability decay per day since last practice
CONFIDENCE_NOISE = 0.35  # how noisy confidence reporting is
RESPONSE_TIME_BASE = 18.0  # baseline seconds for an easy question

random.seed(42)  # reproducible synthetic data

# ─── DOMAIN SETUP ────────────────────────────────────────────────────────────


def build_concepts(n: int) -> list[dict]:
    """Synthetic concept buckets — purely for the ability-simulation model, since the
    real bank has no per-question topic/difficulty taxonomy yet (see module docstring).
    """
    return [
        {
            "cui": f"C{i:03d}",
            "irt_difficulty": random.gauss(0, 0.7),
            "is_root": i < n // 4,
        }
        for i in range(n)
    ]


def assign_questions_to_concepts(
    questions: list[Question], concepts: list[dict]
) -> dict[str, list[Question]]:
    """Randomly buckets the real question bank across synthetic concepts, so each
    concept has a pool of real, bank-backed questions to draw from."""
    shuffled = list(questions)
    random.shuffle(shuffled)
    buckets: dict[str, list[Question]] = {c["cui"]: [] for c in concepts}
    for i, q in enumerate(shuffled):
        buckets[concepts[i % len(concepts)]["cui"]].append(q)
    return buckets


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
        ability[c["cui"]] = random.gauss(prior, 0.6)
    return {
        "base_aptitude": base_aptitude,
        "ability": ability,
        "last_practiced": {},
    }


def apply_forgetting(model: dict, cui: str, now: datetime) -> None:
    """Decay ability toward 0 based on days since last practice."""
    last = model["last_practiced"].get(cui)
    if last is None:
        return
    days_gap = (now - last).total_seconds() / 86400
    decay = FORGETTING_RATE * days_gap
    model["ability"][cui] = model["ability"][cui] * math.exp(
        -decay * 0.3
    ) - decay * 0.5 * (1 if model["ability"][cui] > 0 else 0)


def irt_probability(ability: float, difficulty: float) -> float:
    """2-parameter logistic IRT: P(correct) = sigmoid(ability - difficulty)."""
    z = ability - difficulty
    return 1.0 / (1.0 + math.exp(-z))


def simulate_quiz_answer(
    model: dict, question: Question, cui: str, difficulty: float, now: datetime
) -> dict:
    """Simulate one quiz answer: correctness, selected option, confidence, response time.
    Updates the student's ability in place (learning effect)."""
    apply_forgetting(model, cui, now)

    ability = model["ability"][cui] + model["base_aptitude"] * 0.3
    p_correct = irt_probability(ability, difficulty)
    correct = random.random() < p_correct

    if correct:
        model["ability"][cui] += LEARNING_RATE * (1.2 - p_correct)
    else:
        model["ability"][cui] -= LEARNING_RATE * 0.4 * p_correct
    model["last_practiced"][cui] = now

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
    model: dict, cui: str, difficulty: float, now: datetime
) -> str:
    """Simulate one flashcard self-rating from the same ability/difficulty margin used
    for quiz confidence — a student who'd answer confidently-correct on a concept also
    tends to recall its flashcards as "good"/"easy", and vice versa. Does NOT apply the
    quiz learning-effect update (flashcard recall practice is a separate, lighter-weight
    signal here), but DOES apply forgetting decay and updates last_practiced, since
    flashcard drilling is still practice that should stave off decay."""
    apply_forgetting(model, cui, now)
    ability = model["ability"][cui] + model["base_aptitude"] * 0.3
    model["last_practiced"][cui] = now

    margin = ability - difficulty + random.gauss(0, CONFIDENCE_NOISE)
    if margin > 0.7:
        return "easy"
    if margin > 0.1:
        return "good"
    if margin > -0.5:
        return "hard"
    return "again"


# ─── SESSION / SEQUENCE GENERATION ────────────────────────────────────────────


def generate_and_write_student(
    driver,
    student_id: str,
    concepts: list[dict],
    concept_questions: dict[str, list[Question]],
) -> dict:
    """Generates and writes one student's full multi-session interaction history straight
    to Neo4j via the real quiz/flashcard write functions, backdated across sessions/days.
    Returns a small summary dict for the run's final report."""
    model = init_student_model(concepts)
    n_sessions = random.randint(*SESSIONS_PER_STUDENT)
    current_time = START_DATE + timedelta(days=random.randint(0, 10))

    root_cuis = [c["cui"] for c in concepts if c["is_root"]]
    other_cuis = [c["cui"] for c in concepts if not c["is_root"]]
    difficulty_by_cui = {c["cui"]: c["irt_difficulty"] for c in concepts}

    n_quiz_answers = 0
    n_flashcard_reviews = 0

    for session_idx in range(n_sessions):
        n_in_session = random.randint(*INTERACTIONS_PER_SESSION)
        progress = session_idx / max(1, n_sessions - 1)
        root_weight = max(0.15, 0.75 - progress * 0.6)
        is_flashcard_session = random.random() < FLASHCARD_SESSION_RATE

        session_id = None
        if not is_flashcard_session:
            session_id = start_session(
                driver, student_id, topic_path="Master MCQ", ts=current_time
            )

        for _ in range(n_in_session):
            if random.random() < root_weight and root_cuis:
                target_cui = random.choice(root_cuis)
            else:
                target_cui = random.choice(other_cuis if other_cuis else root_cuis)

            candidates = concept_questions.get(target_cui) or []
            if not candidates:
                continue
            question = random.choice(candidates)
            difficulty = difficulty_by_cui[target_cui]

            current_time += timedelta(minutes=random.uniform(0.5, 4))

            if is_flashcard_session:
                rating = simulate_flashcard_rating(
                    model, target_cui, difficulty, current_time
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
                result = simulate_quiz_answer(
                    model, question, target_cui, difficulty, current_time
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

        if session_id is not None:
            end_session(driver, student_id, session_id, ts=current_time)

        current_time += timedelta(days=random.uniform(*SESSION_GAP_DAYS))

    return {
        "n_sessions": n_sessions,
        "n_quiz_answers": n_quiz_answers,
        "n_flashcard_reviews": n_flashcard_reviews,
    }


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--n-students", type=int, default=N_STUDENTS)
    ap.add_argument("--n-concepts", type=int, default=N_CONCEPTS)
    ap.add_argument(
        "--dry-run", action="store_true", help="print the plan without writing to Neo4j"
    )
    args = ap.parse_args()

    bank = load_question_bank()
    all_questions = [
        q for topic in bank.topics() for q in bank.questions_for_topic(topic)
    ]
    log.info("loaded %d real questions from the bank", len(all_questions))

    concepts = build_concepts(args.n_concepts)
    concept_questions = assign_questions_to_concepts(all_questions, concepts)

    if args.dry_run:
        log.info(
            "dry run: would enroll %d students, %d concepts, %d questions bucketed",
            args.n_students,
            args.n_concepts,
            len(all_questions),
        )
        return

    load_dotenv()
    driver = make_driver()
    ensure_constraints(driver)
    try:
        totals = {"n_sessions": 0, "n_quiz_answers": 0, "n_flashcard_reviews": 0}
        for i in range(args.n_students):
            student_id = enroll_student(
                driver,
                full_name=f"Synthetic Student {i:04d}",
                student_number=f"SYNTH-{i:04d}",
                academic_year=random.randint(1, 6),
            )
            summary = generate_and_write_student(
                driver, student_id, concepts, concept_questions
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
