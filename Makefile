COMPOSE = docker compose -f docker-compose.yml --env-file .env

.PHONY: setup mlflow-up mlflow-down mlflow-logs mlflow-restart minio-up minio-down minio-logs neo4j-up neo4j-down neo4j-logs up down logs restart ps clean psql populate-postgres populate-neo4j populate

# Copy .env.example -> .env on first run (skipped if already present)
setup:
	test -f .env || cp .env.example .env

# MLflow tracking server (postgres backend + s3/minio artifact store)
mlflow-up: setup
	$(COMPOSE) up -d postgres minio minio-init mlflow

mlflow-down:
	$(COMPOSE) stop mlflow

mlflow-logs:
	$(COMPOSE) logs -f mlflow

mlflow-restart:
	$(COMPOSE) restart mlflow

# MinIO object store (+ bucket bootstrap)
minio-up: setup
	$(COMPOSE) up -d minio minio-init

minio-down:
	$(COMPOSE) stop minio

minio-logs:
	$(COMPOSE) logs -f minio

# Neo4j (student knowledge graph)
neo4j-up: setup
	$(COMPOSE) up -d neo4j

neo4j-down:
	$(COMPOSE) stop neo4j

neo4j-logs:
	$(COMPOSE) logs -f neo4j

# Full stack
up: setup
	$(COMPOSE) up -d

down:
	$(COMPOSE) down

restart:
	$(COMPOSE) restart

logs:
	$(COMPOSE) logs -f

ps:
	$(COMPOSE) ps

clean:
	$(COMPOSE) down -v

server:
	uvicorn app.main:app --reload --reload-dir app --host 0.0.0.0 --port 8000

# Connect to postgres via psql inside the container. Override db, e.g. `make psql db=other`
db ?= mlflow
psql:
	$(COMPOSE) exec postgres psql -U $${POSTGRES_USER:-mlflow} -d $(db)

# Load the MCQ question bank into postgres (src/quiz/bank.py's source of truth)
populate-postgres: mlflow-up
	$(COMPOSE) up -d --wait postgres
	uv run python -m scripts.populate_mcq_postgres

# Load src/domain_kg/graph.nq into neo4j via n10s
populate-neo4j: neo4j-up
	$(COMPOSE) up -d --wait neo4j
	uv run python -m src.domain_kg.load_neo4j

populate-students: neo4j-up
	$(COMPOSE) up -d --wait neo4j
	uv run python -m scripts.generate_dummy_interactions

# Populate both postgres (question bank) and neo4j (domain knowledge graph)
populate: populate-postgres populate-neo4j
