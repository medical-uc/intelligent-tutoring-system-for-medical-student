# Intelligent Tutoring System for Medical Student

An intelligent tutoring system for medical students. Two connected halves: an offline
pipeline that turns PDF lecture slides into a bank of multiple-choice questions, and a
live FastAPI service that serves those questions to students and tracks their answers in
a Neo4j graph.

**Start here:** [docs/01-architecture.md](docs/01-architecture.md) for the big picture,
or [docs/07-operations.md](docs/07-operations.md) if you just want to run it.

## Quickstart

```bash
make neo4j-up
.venv/bin/uvicorn app.main:app --reload
```

See [docs/07-operations.md](docs/07-operations.md) for environment setup, the full
Makefile, and running the content pipeline.

## Documentation

| Doc | Covers |
| --- | --- |
| [01-architecture.md](docs/01-architecture.md) | System overview, repo map, how the two halves connect |
| [02-conventions.md](docs/02-conventions.md) | Cross-cutting coding patterns used throughout |
| [03-ingestion-pipeline.md](docs/03-ingestion-pipeline.md) | PDF → chunked document (MinerU, post-processing, captioning) |
| [04-mcq-generation.md](docs/04-mcq-generation.md) | Chunks → question bank (triple extraction, distractors, self-critique) |
| [05-serving-api.md](docs/05-serving-api.md) | FastAPI app, auth, quiz endpoints |
| [06-student-graph.md](docs/06-student-graph.md) | Neo4j schema: students, sessions, attempts |
| [07-operations.md](docs/07-operations.md) | Running everything, known gaps |
| [08-domain-kg-pipeline.md](docs/08-domain-kg-pipeline.md) | Standalone staged KG pipeline: textbook → UMLS-linked RDF graph → MCQs |
| [09-knowledge-tracing.md](docs/09-knowledge-tracing.md) | Personalization engine: server-side BKT prototype, per-student weak-topic ranking |
| [HANDOVER.md](docs/HANDOVER.md) | Project handover — status, open decisions, ops runbook, next steps |

## License

Proprietary. All rights reserved. See [LICENSE](LICENSE) for details.
