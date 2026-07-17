# Airflow runtime image for the Azure Data Platform pipelines.
#
# This repo owns the image: the pipeline dependency set lives in pyproject.toml +
# uv.lock (managed by `uv`), so a `uv add <pkg>` flows into the image on the next
# build. DAGs and plugins are NOT baked — they are delivered at runtime (git-sync on
# AKS, bind-mount locally), so only pyproject.toml + uv.lock enter the build context.
#
# Stays on the classic builder: CI ships this via `az acr build`, and ACR Tasks has no
# BuildKit ("the --mount option requires BuildKit"). No RUN --mount here for that reason.

ARG AIRFLOW_VERSION=3.2.2
FROM apache/airflow:slim-${AIRFLOW_VERSION}-python3.12

# PYTHONDONTWRITEBYTECODE=1 stops runtime processes writing .pyc into bind-mounted
# dags/ and plugins/; the installs below pass --compile-bytecode so site-packages .pyc
# are baked at build instead. Both are needed — the env var alone means every process
# recompiles every import from source, forever, since nothing may cache the result.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# uv ships in the base image (AIRFLOW_UV_VERSION), pinned to the same release that
# resolved the base's own env — no install layer, and no floating `:latest` tag.

# Install the locked runtime deps. uv.lock is the single source of truth, so the
# container matches the local `.venv` exactly. Notes:
#   --no-dev          exclude the dev group (pytest/faker/pandas-for-tests). Anything a
#                     DAG imports at runtime must live in [project.dependencies], not dev.
#   --no-emit-project don't install the adp-pipelines project itself (its DAGs/plugins
#                     are mounted, not packaged).
#   --require-hashes  verify every wheel against the sha256 in uv.lock, and fail closed if
#                     any requirement lacks one. A dep with no hash (git/local/editable
#                     source) breaks the build by design — pin it to a released version
#                     rather than dropping this flag.
#   --no-cache        the base image points UV_CACHE_DIR at /tmp/.cache/uv, which would
#                     otherwise bake ~800MB of wheels into the layer.
#   --compile-bytecode  pip compiled .pyc on install by default; uv does not. Without this
#                     the image ships no bytecode and, given PYTHONDONTWRITEBYTECODE above,
#                     could never build any.
# apache-airflow==3.2.2 is already in the base image, so uv sees it satisfied and only
# adds the delta (azure provider + azure/office365 libs). The lock was resolved with
# apache-airflow==3.2.2 pinned, so every version here is core-compatible.
COPY pyproject.toml uv.lock /opt/airflow/
RUN uv export --frozen --no-dev --no-emit-project \
    | uv pip install --compile-bytecode --require-hashes --no-cache -r - \
    && rm /opt/airflow/pyproject.toml /opt/airflow/uv.lock

# Install the provider-registration shim.
# --no-deps: it has none, and this keeps it from ever pulling in a resolver pass against the deps above.
# --chown: plain COPY creates new dirs as root:root 755,
# which the airflow user (uid 50000, gid 0) can't write to even though it belongs to group root — unlike /opt/airflow
# itself, which the base image already made group-writable. Owning it directly sidesteps that.
COPY --chown=airflow:0 provider_registration /opt/airflow/provider_registration
RUN uv pip install --compile-bytecode --no-deps --no-cache /opt/airflow/provider_registration \
    && rm -rf /opt/airflow/provider_registration
