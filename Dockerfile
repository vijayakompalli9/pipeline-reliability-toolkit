FROM python:3.11-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY configs ./configs
RUN pip install --no-cache-dir --no-deps .

RUN useradd --create-home appuser && chown -R appuser /app
USER appuser

# Default: run the offline demo. Override with e.g.
#   docker run IMAGE reliability check --contract ... --data ...
CMD ["python", "-m", "reliability.demo", "--out", "/app/out", "--data-dir", "/app/data"]
