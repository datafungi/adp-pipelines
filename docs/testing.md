# Testing

How this repo's tests are organised, and which failure each tier exists to catch.

## Why the tiers look like this

An Airflow deployment is not one artifact. Three converge in the pod at runtime, on
independent lifecycles:

| Artifact | Carries | Delivered by |
|---|---|---|
| Image | `uv.lock` runtime deps, `provider_registration` | `az acr build`, from this repo's CI |
| Git ref | `dags/`, `plugins/` | git-sync on AKS, bind-mount locally |
| Key Vault | Connections, Variables | `AzureKeyVaultBackend` |

Nothing checks across those boundaries. Hooks are git-synced; the `provider_registration`
shim describing their connection schemas is baked into the image. They ship on different
cadences and can disagree without failing loudly.

Local development collapses all three — one bind-mount, one `.env`, one locally built
image — so the seam is invisible there.

The tiers are therefore organised by what can fail, not by "unit / integration / e2e".
Each gates the cheapest boundary at which its failure is observable.

## The principle

**Test at the lowest tier that can catch the failure.** Two corollaries:

- *Deterministic tests assert logic; live tests assert connections.* Mixing them yields
  slow tests that fail for reasons unrelated to what they name.
- *Each tier gates one boundary.* A tier gating nothing is documentation; a tier gating
  two is a bottleneck.

## The tiers

| Tier | Gates | Needs |
|---|---|---|
| [Static](#static) | merge | nothing |
| [Unit](#unit) | merge | nothing |
| [Contract](#contract) | merge | nothing |
| [Image](#image) | image push | Docker |
| [Live](#live) | credential / grant changes | Key Vault + the live service |
| [Deploy smoke](#deploy-smoke) | environment promotion | a deployed cluster |

Infrastructure tests live with the infrastructure, in `azure-data-platform`.

## Static

Four checks, no execution, no dependencies. Configured in `pyproject.toml`, run as
labelled steps of one CI job (`.github/workflows/ci.yml`); the rationale for each lives
there.

| Check | Command | Scope |
|---|---|---|
| Lint | `ruff check .` | everything |
| Format | `ruff format --check .` | everything |
| Types | `mypy` | `plugins`, `dags`, `provider_registration` |
| Security | `bandit -r plugins dags provider_registration` | source only |

`uv sync --locked` fails on a lock stale against `pyproject.toml`, so dependency drift is
caught here too.

## Unit

pytest. No network, credentials, Airflow runtime, or cluster. `tests/` mirrors the source
layout, one file per module.

**Unit owns every deterministic assertion.** A provider's parsing and transform logic is
deterministic given bytes; it belongs here, never behind a live connection.

**Fixtures are built in-process, never committed.** A committed binary fixture is opaque
to review and editable by anyone who opens it.

**Doubles stay minimal.** A double that answers everything proves nothing.

**DAG tests parse; they don't run.** `DagBag` imports the module; tests assert the DAG
registers, reads its `config.toml`, and carries the intended schedule and task
dependencies. Import errors are asserted first — a DAG that fails to import is invisible
in the UI and reports nothing.

## Contract

pytest, no credentials. Unit tests one artifact's logic; Contract tests agreement between
the three artifacts above, which have no compiler in common.

**Runtime imports resolve against `[project.dependencies]`.** The image installs
`--no-dev`, so anything `dags/` or `plugins/` imports must be a runtime dependency. Static
and Unit both run in the `uv sync` virtualenv, which carries the dev group — a dev-only
import passes both and fails only on AKS.

**`provider_registration` agrees with the hooks it describes.** The shim is baked into the
image; hooks are git-synced. Assert the declared connection fields against the attributes
each hook reads — nothing else stops the two describing different schemas.

**Every DAG folder satisfies the `config.toml` convention.** One test iterates `dags/*/`,
so a new folder cannot skip validation by omitting its own test file.

Each contract test lives with the artifact whose fixtures exercise it, not in a central
file — a hook's registration contract needs that hook's constructor and auth doubles, and
a central file would have to know every provider's internals.

Connection *existence* is not checked here: a name resolves per-environment, and only the
target environment can answer for it. That belongs to [Deploy smoke](#deploy-smoke).

## Image

Runs on the CI runner against the built image, before push: `docker build` → assert →
`docker push` only if green. Building inside ACR cannot gate this way, since it pushes as
a side effect of building.

**Runtime imports resolve inside the image.** Contract checks this statically against the
lock; only the built image sees a base-image bump dropping a package the lock assumed was
transitively present.

**`provider_registration` is discoverable.** It is pip-installed at build — assert Airflow
finds the connection types it registers.

**DAGs parse inside the image**, with `dags/` and `plugins/` mounted as git-sync delivers
them. This is the tier's core: the only place the real delivery topology exists before a
cluster does — shipped image, no dev group, code mounted rather than baked.

Depends on the registry staying publicly reachable. A private endpoint would put it out of
a hosted runner's reach and force the build back inside ACR, taking the gate with it.

## Live

pytest, marked `integration` and excluded by the `-m 'not integration'` default — opt-in by
construction. Run on demand, before trusting a credential or grant change:

```bash
pytest -m integration
```

**Owns only what needs a real credential and a real endpoint:** token acquisition, and the
cheapest read proving the grant reaches content. Auth alone proves a token was issued, not
that it reaches anything — that gap surfaces at the service and reads like a permissions
problem.

**Nothing deterministic lives here.** Anything that holds against bytes belongs to Unit.
This tier attracts scope creep, and each addition buys a slow test that fails for reasons
unrelated to what it names.

Runs against the dev Key Vault, which persists across teardown, so Live is available while
no cluster is. It proves the code path against the live service — never an environment's
identity, since local credentials and AKS workload identity are separate paths. That
belongs to [Deploy smoke](#deploy-smoke).

## Deploy smoke

A DAG, triggered by CD after each deploy, gating promotion to the next environment. It
executes in the pod, as the pod — run from a runner it would prove nothing the tiers above
do not.

**Asserts auth and one minimal read**, the same shape as Live and sharing its assertion
helper. The question differs: Live asks whether a credential and grant are intact, smoke
asks whether *this* pod, with *this* identity, reaches the service.

**Owns connection existence.** A connection name resolves per-environment; only the target
environment can answer whether it is there.

**The only tier where all three artifacts are real at once** — deployed image, git-synced
ref, environment Key Vault. Every seam the tiers above check in isolation is live here
together.

The trigger mechanism is unsettled: the api-server authenticates via Entra SSO, so a CD
job cannot mint a token by password. The requirement stands regardless — CD triggers it
post-deploy and blocks promotion on the result.

## Current state

Static and Unit are in place. Contract, Image and Live need building. Deploy smoke is
blocked on infrastructure that does not exist yet.

- [x] Static — ruff, format, mypy, bandit in CI
- [x] Unit — hooks, config loader, provider registration, DAG parse
- [x] Contract — `provider_registration` agreement (hook class path, `conn_type`, and the
      field set `get_conn()` reads against the form the registration declares); runtime
      imports resolve against `[project.dependencies]`; `config.toml` convention across
      `dags/*/`
- [x] Image — `build-image.yml` builds on the runner, asserts against the built image,
      then pushes
- [ ] Drop the `Container Registry Tasks Contributor` grant in `azure-data-platform`'s
      `shared-acr` stack, once the above is merged and `az acr build` is no longer run
- [x] Live — `integration` marker wired against the dev Key Vault
- [x] Retire the read-parsing check DAG — its assertions were deterministic and already
      covered by Unit
- [ ] Deploy smoke — `sharepoint_connection_check` becomes the smoke DAG; blocked on:
  - [ ] Key Vault secrets backend wired on AKS
  - [ ] git-sync enabled
  - [ ] an environment to deploy to, and a validated trigger mechanism
