# -- Stage 1: build the React dashboard --------------------------------
# Separate build stage so the final image doesn't need Node at all --
# only the built static output (dist/) gets copied into the Python image.
FROM node:20-slim AS frontend-build
WORKDIR /dashboard
COPY dashboard/package.json ./
RUN npm install
COPY dashboard/ ./
RUN npm run build

# -- Stage 2: the actual Python app -------------------------------------
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Bakes the dashboard's built assets in at app/static/dashboard -- see
# app/main.py's dashboard_app() route, which serves this at /dashboard.
COPY --from=frontend-build /dashboard/dist ./app/static/dashboard

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
