"""Image tier: assert the artifact that ships, not the virtualenv that tested it.

Every check runs inside IMAGE_REF via `docker run`, because the image excludes the dev
group and so carries no pytest. The workflow builds, runs these, and pushes only if they
pass. See docs/testing.md.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.image

ROOT = Path(__file__).parents[1]
# Where git-sync delivers them on AKS, and the bind-mount locally.
_DAGS = "/opt/airflow/dags"
_PLUGINS = "/opt/airflow/plugins"


@pytest.fixture(scope="session")
def image_ref() -> str:
    ref = os.getenv("IMAGE_REF")
    if not ref:
        pytest.skip("IMAGE_REF not set -- build the image first")
    return ref


def _run(image_ref: str, script: str, *, deliver_code: bool = False) -> str:
    """Run `script` inside the image, optionally mounting dags/ and plugins/."""
    command = ["docker", "run", "--rm"]
    if deliver_code:
        command += [
            "-v",
            f"{ROOT / 'dags'}:{_DAGS}:ro",
            "-v",
            f"{ROOT / 'plugins'}:{_PLUGINS}:ro",
        ]
    command += [image_ref, "python", "-c", script]

    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, (
        f"exited {result.returncode}\n--- stdout ---\n{result.stdout}"
        f"\n--- stderr ---\n{result.stderr}"
    )
    return result.stdout


def test_third_party_imports_resolve(image_ref, third_party_imports):
    """The Contract tier checks this against the lock; only the image sees a base-image
    bump dropping a package the lock assumed was transitively present. Covers the
    function-local imports a DAG parse never reaches.
    """
    imports = "; ".join(f"import {module}" for module in third_party_imports)
    _run(image_ref, f"{imports}\nprint('ok')")


def test_registered_connection_types_resolve(image_ref):
    """Every connection type the baked shim declares must resolve to its hook class in
    the delivered code. Unresolved hooks read back as None rather than raising, so a
    broken hook-class-name would otherwise surface as an empty connection form.
    """
    output = _run(
        image_ref,
        """
from adp_provider_reg.get_provider_info import get_provider_info
from airflow.sdk.providers_manager_runtime import ProvidersManagerTaskRuntime

declared = {e["connection-type"] for e in get_provider_info()["connection-types"]}
hooks = ProvidersManagerTaskRuntime().hooks
unresolved = sorted(name for name in declared if hooks.get(name) is None)
assert not unresolved, f"declared but did not resolve to a hook class: {unresolved}"
print(sorted(declared))
""",
        deliver_code=True,
    )
    assert "sharepoint" in output


def test_dags_parse(image_ref):
    """The only place the real delivery topology exists before a cluster does: the
    shipped image, no dev group, code mounted rather than baked.
    """
    _run(
        image_ref,
        f"""
from airflow.models.dagbag import DagBag

bag = DagBag(dag_folder="{_DAGS}", include_examples=False)
assert not bag.import_errors, bag.import_errors
assert bag.dags, "no DAGs found -- the mount or the folder layout is wrong"
print(sorted(bag.dags))
""",
        deliver_code=True,
    )
