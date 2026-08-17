# Minimal image for the SeaweedFS write relay -- see main.py's own
# docstring for the full reasoning. Deliberately as small/simple as
# possible: one Python file, three dependencies, nothing else running
# in this container.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

# Shell form so ${PORT} actually expands -- Render injects PORT at
# runtime; JSON-array CMD form does not perform variable substitution.
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
