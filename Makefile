COMPOSE = docker compose -f docker-compose.yml --env-file .env

.PHONY: setup mlflow-up mlflow-down mlflow-logs mlflow-restart minio-up minio-down minio-logs neo4j-up neo4j-down neo4j-logs up down logs restart ps clean psql

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

# Connect to postgres via psql inside the container. Override db, e.g. `make psql db=mlflow`
db ?= app_db
psql:
	$(COMPOSE) exec postgres psql -U $${POSTGRES_USER:-mlflow} -d $(db)
