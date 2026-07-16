# adp-pipelines

Airflow DAGs, plugins, and the Airflow **runtime image** for the Azure Data Platform.

This repo owns the image because it owns the dependency set: `pyproject.toml` + `uv.lock`
(managed by [uv](https://docs.astral.sh/uv/)) are the single source of truth for what is
installed, so `uv add <pkg>` reaches the local stack and AKS from one edit. DAGs and
plugins are **not** baked — git-sync on AKS, bind-mount locally.

## Local development

See the header of [`docker-compose.yml`](docker-compose.yml). Short version:

```bash
cp .env.example .env            # fill KV_CLIENT_ID / KV_CLIENT_SECRET
echo "AIRFLOW_UID=$(id -u)" >> .env
mkdir -p logs
docker compose up airflow-init  # db migrate + create admin/admin
docker compose up --build       # http://localhost:8080  (admin / admin)
```

## CI

| Workflow | Trigger | Does |
|----------|---------|------|
| [`ci.yml`](.github/workflows/ci.yml) | every push/PR | `uv sync --locked` (fails on lock drift), `ruff check`, `ruff format --check`, `mypy`, `bandit`, `pytest` (unit only) |
| [`build-image.yml`](.github/workflows/build-image.yml) | image inputs change | PR: `docker build` validation. Merge to `main`: `az acr build` → `cradpsea01`, tagged `airflow:<sha>` + `airflow:dev` |

`build-image` only fires on what the image actually bakes (`Dockerfile`, `pyproject.toml`,
`uv.lock`, `provider_registration/`, `.dockerignore`) — a DAG change rebuilds nothing.

Pushes authenticate by **OIDC federation**, no stored credentials: the workflow mints a
GitHub token that Azure trades for an ARM token, trusted only for this repo's `main`
branch — subject `repo:datafungi@193031567/adp-pipelines@1301127872:ref:refs/heads/main`.
The id suffixes are GitHub's *immutable subject claim* format, automatic for repos created
on or after 2026-07-15 (this one, by 22 hours); read it with
`gh api repos/datafungi/adp-pipelines/actions/oidc/customization/sub --jq .sub_claim_prefix`
rather than hand-writing it. The identity, its two roles, and the required repo variables
(`AZURE_CLIENT_ID` / `AZURE_TENANT_ID` / `AZURE_SUBSCRIPTION_ID`) are provisioned by the
`shared-acr` Terraform stack in the `azure-data-platform` repo.

Deploying a build: pin the `<sha>` tag in that repo's `helm/airflow/values-dev.yaml`.
`:dev` is a moving tag for local docker-compose and the dev-cluster MVP only.

## Checks

Everything CI runs, runnable locally — same commands, no surprises:

```bash
uv run ruff check . && uv run ruff format .   # lint + format
uv run mypy                                   # types (config in [tool.mypy])
uv run bandit -r plugins dags provider_registration   # security scan
uv run pytest                                 # unit (default: -m 'not integration')
uv run pytest -m integration                  # hits live SharePoint/Key Vault; needs .env
```

`mypy` runs with `disallow_untyped_defs`, so new functions need annotations.
`office365` ships no type stubs — values off its objects are `Any`, so annotate at the
boundary (`title: str = web.properties["Title"]`) rather than letting `Any` leak.

`bandit` fails on any finding. Its `B105` ("hardcoded password") fires on **any** dict
key named `password`, including Airflow connection-form labels — suppress a genuine
false positive at the line with `# nosec B105` plus a reason, never by lowering the
severity threshold.
