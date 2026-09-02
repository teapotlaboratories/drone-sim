"""Every dependency the suite needs must be installed by the CI workflow.

THE FAILURE THIS EXISTS FOR. `numpy` was added to `tests/test_depth_view.py` and not to
`.github/workflows/checks.yml`. The full suite passed locally -- this machine has numpy --
and the required check went red on the pull request with a collection error, which is a
slower and more embarrassing way to find out.

It is the same shape as the rule already learned about `vendor/`: **a test that needs
something the runner does not have can only ever pass on the developer's machine.** That one
was about reading a vendored file; this one is about importing a library. Both look green
locally and both are only ever red where it counts.

Guarded imports are exempt: `pytest.importorskip` is the documented way to say "this test
needs something optional", and it degrades to a skip rather than a collection error.
"""
import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TESTS = REPO / "tests"
WORKFLOW = REPO / ".github/workflows/checks.yml"


def _install_line() -> str:
    for line in WORKFLOW.read_text().splitlines():
        if "pip install" in line:
            return line
    raise AssertionError("no `pip install` line in the workflow")


def _top_level_imports(path: Path) -> set[str]:
    """Module names imported at the TOP LEVEL of a file.

    Top level only: an import inside a function runs when the test runs, so it fails that one
    test. An import at module scope fails COLLECTION, which takes the whole suite down with
    it -- exit code 2, and no other result reported."""
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def _guarded(path: Path) -> set[str]:
    """Modules obtained through pytest.importorskip, which degrade to a skip."""
    out: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "importorskip" and node.args
                and isinstance(node.args[0], ast.Constant)):
            out.add(str(node.args[0].value).split(".")[0])
    return out


def test_every_unguarded_third_party_import_is_installed_by_ci():
    line = _install_line()
    missing = {}
    for f in sorted(TESTS.glob("test_*.py")):
        for mod in _top_level_imports(f) - _guarded(f):
            if mod in sys.stdlib_module_names or mod == "pytest":
                continue
            # The workflow installs distribution names; `yaml` ships as PyYAML.
            dist = {"yaml": "pyyaml"}.get(mod, mod)
            if dist not in line.lower():
                missing.setdefault(dist, []).append(f.name)
    assert not missing, (
        "these imports are not installed by .github/workflows/checks.yml, so the required "
        f"check will fail on collection while the suite passes locally: {missing}")


def test_the_workflow_still_installs_what_it_claims():
    """A guard on the guard: if the pip line stops installing pytest, this file's premise
    (that the suite runs at all in CI) is gone."""
    line = _install_line()
    for pkg in ("pytest", "pyyaml", "numpy"):
        assert pkg in line.lower(), f"{pkg} disappeared from the CI install line"
