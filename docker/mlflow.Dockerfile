FROM python:3.13-slim

RUN pip install --no-cache-dir \
    mlflow==2.20.2 \
    psycopg2-binary==2.9.10 \
    boto3==1.35.99

EXPOSE 5000

ENTRYPOINT ["mlflow", "server", "--host", "0.0.0.0", "--port", "5000"]
