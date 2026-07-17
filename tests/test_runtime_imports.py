"""Contract: everything dags/ and plugins/ import is a declared runtime dependency.

The image installs `uv export --no-dev`, so a dev-group import reaches AKS as an
ImportError. Static and Unit both run in the `uv sync` virtualenv, which carries the dev
group, and cannot see it. See docs/testing.md.

Declared, not merely installed: a transitive dependency is one resolution away from
vanishing, which is why pandas is pinned in pyproject.toml despite shipping in the
Airflow base image.
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from importlib.metadata import packages_distributions
from pathlib import Path

ROOT = Path(__file__).parents[1]

# Airflow puts plugins/ and dags/ on sys.path at runtime; provider_registration is
# pip-installed separately with --no-deps, so it answers to a different rule.
_DELIVERED = ("plugins", "dags")
# Mirrors pytest's `pythonpath`: imports resolving here are first-party, not packages.
_SOURCE_ROOTS = ("plugins", "dags", "provider_registration")


def _canonical(name: str) -> str:
    """PEP 503 distribution name."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _declared_dependencies() -> set[str]:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    return {
        _canonical(re.split(r"[=<>~!\[; ]", spec, maxsplit=1)[0])
        for spec in pyproject["project"]["dependencies"]
    }


def _first_party() -> set[str]:
    return {
        path.stem if path.suffix == ".py" else path.name
        for root in _SOURCE_ROOTS
        for path in (ROOT / root).iterdir()
        if not path.name.startswith((".", "_"))
    }


def _imports() -> dict[str, set[str]]:
    """Top-level module name -> the files importing it."""
    found: dict[str, set[str]] = {}
    for root in _DELIVERED:
        for path in (ROOT / root).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and not node.level:
                    names = [node.module or ""]
                else:
                    continue
                for name in names:
                    top = name.split(".")[0]
                    if top:
                        found.setdefault(top, set()).add(str(path.relative_to(ROOT)))
    return found


def test_runtime_imports_are_declared_dependencies():
    declared = _declared_dependencies()
    first_party = _first_party()
    distributions = packages_distributions()

    offenders = []
    for module, files in sorted(_imports().items()):
        if module in sys.stdlib_module_names or module in first_party:
            continue

        provided_by = {_canonical(dist) for dist in distributions.get(module, [])}
        if provided_by & declared:
            continue

        source = ", ".join(sorted(provided_by)) or "nothing installed"
        offenders.append(
            f"  {module} ({source}) — imported by {', '.join(sorted(files))}"
        )

    assert not offenders, (
        "imported by dags/ or plugins/ but not in [project.dependencies], so absent "
        "from the image:\n" + "\n".join(offenders)
    )
