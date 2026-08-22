"""Compliance test: the agent must reach Alpaca through the CLI, not the SDK.

The hackathon requires projects to use Alpaca's MCP server or its CLI tools.
Importing `alpaca-py` anywhere in the agent path silently reintroduces the
direct-SDK dependency that fails that requirement, and it would do so without
breaking a single behavioural test — which is exactly why this check exists.

If this test fails, the fix is to route the call through
`options_agent.broker.Broker`, not to relax the test.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

AGENT_ROOT = Path(__file__).resolve().parent.parent / "options_agent"

# The transport module names contain the string "alpaca" legitimately; what is
# forbidden is importing the `alpaca` SDK package itself.
FORBIDDEN_ROOTS = {"alpaca"}


def _python_files() -> list[Path]:
    return sorted(AGENT_ROOT.rglob("*.py"))


def test_agent_path_has_python_files():
    """Guard against the glob silently matching nothing and passing vacuously."""
    assert _python_files(), f"No Python files found under {AGENT_ROOT}"


@pytest.mark.parametrize("path", _python_files(), ids=lambda p: p.name)
def test_module_does_not_import_the_alpaca_sdk(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in FORBIDDEN_ROOTS:
                    offenders.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            # A relative import (level > 0) of our own `alpaca_cli` module is
            # fine; only absolute imports of the `alpaca` package are not.
            if node.level == 0 and node.module and node.module.split(".")[0] in FORBIDDEN_ROOTS:
                offenders.append(f"from {node.module} import ...")

    assert not offenders, (
        f"{path.relative_to(AGENT_ROOT.parent)} imports the alpaca-py SDK: {offenders}. "
        "All Alpaca access must go through options_agent.broker.Broker, which uses the CLI."
    )


def test_requirements_do_not_pin_the_sdk():
    for name in ("requirements.txt", "pyproject.toml"):
        text = (AGENT_ROOT.parent / name).read_text(encoding="utf-8")
        # Strip comments: the files explain *why* the SDK is absent, and that
        # explanation mentions it by name.
        code = "\n".join(
            line for line in text.splitlines() if not line.strip().startswith("#")
        )
        assert "alpaca-py" not in code, f"{name} still declares an alpaca-py dependency"
