COMPOSE = docker compose -f docker/docker-compose.yml --env-file docker/.env

.PHONY: setup mlflow-up mlflow-down mlflow-logs mlflow-restart minio-up minio-down minio-logs up down logs restart ps clean

# Copy .env.example -> .env on first run (skipped if already present)
setup:
	test -f docker/.env || cp docker/.env.example docker/.env

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
