# Azure Data Platform (ADP) Pipelines

Airflow DAGs, plugins, and the Airflow **runtime image** for the Azure Data Platform.

Airflow runs on AKS and orchestrates ingestion into Apache Iceberg on ADLS Gen2, with
Spark doing the heavy transformation. This repo is the orchestration half: the pipelines,
the custom hooks they use, and the image they run on.

## Layout

| Path | What |
|------|------|
| `dags/<dag_name>/` | One folder per DAG: the module plus its `config.toml` |
| `plugins/providers/` | Custom hooks/operators, git-synced onto `sys.path` |
| `plugins/utils/` | Shared helpers |
| `provider_registration/` | pip-installed shim registering custom connection types |
| `docs/` | Setup runbooks |
| `Dockerfile`, `docker-compose.yml` | The runtime image, and a local stack mirroring AKS |
| `tests/` | Mirrors the source tree |

Most of the *why* lives next to the code — module docstrings, the compose header, the
workflow comments. This file is the map.

## Local development

Full notes in [`docker-compose.yml`](docker-compose.yml)'s header. Short version:

```bash
cp .env.example .env             # fill in credentials, generate the shared secrets
echo "AIRFLOW_UID=$(id -u)" >> .env
mkdir -p logs
docker compose up airflow-init   # db migrate + create admin/admin
docker compose up --build        # http://localhost:8080  (admin / admin)
```

Connections and Variables resolve from Azure Key Vault through the same secrets backend
AKS uses, so a connection works locally and in the cluster without being defined twice.

Never `docker compose down -v` — that drops the metadata DB volume.

## Writing a DAG

Each DAG folder carries a `config.toml`, parsed by `utils.dag_config.load_dag_config`.
DAG metadata sits under `[dag]`, per-integration settings under their own section:

```toml
[dag]
display_name = "SharePoint Connection Check"
owner = "data-eng"

[sharepoint]
site_url = "https://datafungi.sharepoint.com/sites/talentacquisition"
sharepoint_conn_id = "sharepoint-default-conn"
```

Config is what varies per DAG; connections are what's shared between them. Anything
scoped to one pipeline belongs here rather than in the connection.

## Plugins

Shared code DAGs import, delivered at runtime rather than baked into the image. Airflow
puts `plugins/` on `sys.path`, so DAGs import from the top level — `from utils.dag_config
import load_dag_config`, not `from plugins.utils...`. pytest's `pythonpath`, mypy's
`mypy_path` and pyright's `extraPaths` all mirror that; they have to agree, or imports
resolve differently in the editor, the test run, and the cluster.

`plugins/providers/` holds custom hooks and operators, one package per external system.

`plugins/utils/` holds helpers that aren't tied to any one system. Conventions worth enforcing across DAGs
belong here, where they're testable in isolation.

A hook also needs an entry in `provider_registration/` for its connection type to reach
the UI: `ProvidersManager` discovers connection types only from installed distributions,
and git-synced plugins are merely on `sys.path`. That shim is pip-installed at image
build and carries the connection metadata; the hooks themselves stay in `plugins/`.

## CI

| Workflow | Trigger | Does |
|----------|---------|------|
| [`ci.yml`](.github/workflows/ci.yml) | every push/PR | `uv sync --locked` (fails on lock drift), `ruff check`, `ruff format --check`, `mypy`, `bandit`, `pytest` (unit only) |
| [`build-image.yml`](.github/workflows/build-image.yml) | image inputs change | PR: `docker build` validation. Merge to `main`: `az acr build` → `cradpsea01`, tagged `airflow:<sha>` + `airflow:dev` |

`build-image` only fires on what the image actually bakes, so a DAG change rebuilds
nothing. Pushes to ACR authenticate by **OIDC federation** — no stored credentials,
trusted only for this repo's `main` branch. The identity, its roles, and the repo
variables it needs are provisioned by the `shared-acr` Terraform stack in the
`azure-data-platform` repo; the workflow header lists them. The federated subject uses
GitHub's immutable id-suffixed format — read it with
`gh api repos/datafungi/adp-pipelines/actions/oidc/customization/sub --jq .sub_claim_prefix`
rather than hand-writing it.

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

`mypy` runs with `disallow_untyped_defs`, so new functions need annotations, and untyped
third-party libraries need annotating at the boundary rather than letting `Any` leak.
`bandit` fails on any finding — annotate a genuine false positive at the line with
`# nosec <test-id>` and a reason, never by lowering the threshold.
