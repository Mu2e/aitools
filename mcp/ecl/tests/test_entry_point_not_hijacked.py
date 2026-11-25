"""Nothing but this package may own the `ecl-mcp` command -- no network.

ecl-api once declared an `ecl-mcp` console script of its own, pointing at its
pre-2.0 FastMCP server. Both distributions install to the same bin/ecl-mcp, so
the dependency won on install order and ecl-mcp.sh silently ran ecl-api's v1
code against the mcp>=2 pinned here -- `ModuleNotFoundError: No module named
'mcp.server.fastmcp'`, in a deployed release, from an install that reported
success. These are the three invariants that would have caught it.
"""

from __future__ import annotations

import importlib.metadata
import sys
from pathlib import Path

import pytest

ECL_DIR = Path(__file__).resolve().parents[1]      # the mcp/ecl project root
BIN = Path(sys.executable).parent                   # this venv's scripts


def _claimants(name: str) -> dict[str, str]:
    """Every installed distribution declaring console script `name`."""
    found = {}
    for dist in importlib.metadata.distributions():
        for ep in dist.entry_points:
            if ep.group == "console_scripts" and ep.name == name:
                # a dist can be installed twice under different paths; last wins
                found[dist.metadata["Name"]] = ep.value
    return found


def test_only_this_package_claims_the_ecl_mcp_command():
    claims = _claimants("ecl-mcp")
    if not claims:
        pytest.skip("ecl-mcp not installed as a distribution (source-tree run)")
    # Deliberately not "ecl_mcp wins" -- a second claimant means whoever
    # installs last wins, so ecl_mcp merely winning today is still the bug.
    assert list(claims) == ["ecl-mcp"], f"ecl-mcp command also claimed by: {claims}"
    assert claims["ecl-mcp"].startswith("ecl_mcp.server")


def test_the_installed_script_runs_this_packages_server():
    """The file that actually executes, whatever the metadata says."""
    script = BIN / "ecl-mcp"
    if not script.is_file():
        pytest.skip(f"no console script at {script}")
    body = script.read_text(encoding="utf-8", errors="replace")
    assert "ecl_mcp.server" in body, f"bin/ecl-mcp does not run ecl_mcp.server:\n{body}"
    assert "ecl_api.mcp" not in body, "bin/ecl-mcp was hijacked by ecl-api"


def test_the_wrapper_targets_a_module_not_a_script_name():
    """ecl-mcp.sh must not exec a bare name a dependency could also claim."""
    # The version-controlled copy first: the installed one in BIN can lag the
    # working tree, and it is the source that must not regress.
    wrapper = ECL_DIR / "scripts" / "ecl-mcp.sh"
    if not wrapper.is_file():
        wrapper = BIN / "ecl-mcp.sh"
    if not wrapper.is_file():
        pytest.skip("ecl-mcp.sh found neither in the source tree nor in BIN")
    exec_lines = [ln for ln in wrapper.read_text().splitlines()
                  if ln.startswith("exec ")]
    assert len(exec_lines) == 1, f"expected one exec line, got {exec_lines}"
    assert "-m ecl_mcp.server" in exec_lines[0], exec_lines[0]


def test_ecl_api_dependency_is_pinned():
    """An unpinned git dep is how the colliding alias arrived unannounced."""
    if sys.version_info < (3, 11):
        pytest.skip("tomllib is 3.11+; this project's floor is 3.10")
    import tomllib

    with (ECL_DIR / "pyproject.toml").open("rb") as fh:
        deps = tomllib.load(fh)["project"]["dependencies"]
    ecl_api = [d for d in deps if d.startswith("ecl-api")]
    assert len(ecl_api) == 1, deps
    url = ecl_api[0].split("@", 1)[1].strip()        # strip the "ecl-api @ "
    # git+https://host/org/repo@<ref> -- the ref is what makes it reproducible
    assert "@" in url.split("://", 1)[1], f"ecl-api is unpinned: {ecl_api[0]}"
