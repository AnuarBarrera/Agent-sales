FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends gcc libpq-dev pandoc \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --no-cache-dir . && rm -rf build *.egg-info
COPY alembic.ini Dockerfile ./
COPY migrations ./migrations
ARG APP_BUILD_REVISION=unknown
LABEL org.opencontainers.image.revision=$APP_BUILD_REVISION
ENV APP_BUILD_REVISION=$APP_BUILD_REVISION
RUN mkdir -p /opt/app-build \
    && python -m app.core.release_identity write-build-manifest \
       --root /app --output /opt/app-build/build_manifest.json \
       --build-revision "$APP_BUILD_REVISION"
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
