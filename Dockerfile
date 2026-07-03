FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY hypofactory/ hypofactory/
COPY webapp/ webapp/
COPY scripts/ scripts/

# База знаний и данные монтируются томами:
#   -v ./artifacts:/app/artifacts -v ./data:/app/data --env-file .env
EXPOSE 8000
CMD ["uvicorn", "webapp.main:app", "--host", "0.0.0.0", "--port", "8000"]
