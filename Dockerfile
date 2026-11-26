FROM python:3.12-slim AS build
WORKDIR /app
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
COPY pyproject.toml ./
COPY src ./src
RUN pip install --prefix=/install .

FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PORT=8000
RUN useradd --create-home --uid 10001 greenlight
COPY --from=build /install /usr/local
COPY alembic.ini ./
COPY migrations ./migrations
COPY src ./src
USER greenlight
EXPOSE 8000
CMD ["sh", "-c", "python -m greenlight.cli prepare && exec uvicorn greenlight.web.app:create_app --factory --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
