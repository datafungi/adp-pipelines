# Airflow runtime image for the Azure Data Platform pipelines.
#
# This repo owns the image: the pipeline dependency set lives in pyproject.toml +
# uv.lock (managed by `uv`), so a `uv add <pkg>` flows into the image on the next
# build. DAGs and plugins are NOT baked — they are delivered at runtime (git-sync on
# AKS, bind-mount locally), so only pyproject.toml + uv.lock enter the build context.
#
# Same image runs locally (docker-compose `build: .`) and on AKS (pulled from ACR):
#   az acr build -r cradpsea01 -t airflow:$SHA -t airflow:dev .
# python3.12 pinned to match uv.lock / .python-version (the base's default tag tracks a
# newer Python); keeping them equal means the container matches the local .venv exactly.
ARG AIRFLOW_VERSION=3.2.2
FROM apache/airflow:${AIRFLOW_VERSION}-python3.12

# uv, to render the locked dependency set into a pip-installable requirements file.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Install the locked runtime deps. uv.lock is the single source of truth, so the
# container matches the local `.venv` exactly. Notes:
#   --no-dev          exclude the dev group (pytest/faker/pandas-for-tests). Anything a
#                     DAG imports at runtime must live in [project.dependencies], not dev.
#   --no-emit-project don't install the adp-pipelines project itself (its DAGs/plugins
#                     are mounted, not packaged).
#   --no-hashes       install over the base image's pre-built env without hash pinning.
# apache-airflow==3.2.2 is already in the base image, so pip sees it satisfied and only
# adds the delta (azure provider + azure/office365 libs). The lock was resolved with
# apache-airflow==3.2.2 pinned, so every version here is core-compatible.
COPY pyproject.toml uv.lock /opt/airflow/
RUN uv export --frozen --no-dev --no-emit-project --no-hashes -o /tmp/requirements.txt \
 && pip install --no-cache-dir -r /tmp/requirements.txt \
 && rm /opt/airflow/pyproject.toml /opt/airflow/uv.lock /tmp/requirements.txt
