"""Contract: everything dags/ and plugins/ import is a declared runtime dependency.

The image installs `uv export --no-dev`, so a dev-group import reaches AKS as an
ImportError. Static and Unit both run in the `uv sync` virtualenv, which carries the dev
group, and cannot see it. See docs/testing.md.

Declared, not merely installed: a transitive dependency is one resolution away from
vanishing, which is why pandas is pinned in pyproject.toml despite shipping in the
Airflow base image.
"""

from __future__ import annotations


def test_runtime_imports_are_declared_dependencies(
    third_party_imports, module_distributions, declared_dependencies, delivered_imports
):
    offenders = []
    for module in third_party_imports:
        provided_by = module_distributions[module]
        if provided_by & declared_dependencies:
            continue

        source = ", ".join(sorted(provided_by)) or "nothing installed"
        files = ", ".join(sorted(delivered_imports[module]))
        offenders.append(f"  {module} ({source}) — imported by {files}")

    assert not offenders, (
        "imported by dags/ or plugins/ but not in [project.dependencies], so absent "
        "from the image:\n" + "\n".join(offenders)
    )
