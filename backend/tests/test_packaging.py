"""The deployment installs a different file from the one developers install.

backend/requirements.txt builds the local virtual environment. pyproject.toml is
what the host resolves. Two lists of the same thing drift the moment nobody is
checking, and the way you find out is a deployment that installed something
different from what you tested against.

So they are checked against each other here. The same reasoning as the two share
stores sharing one record builder: if a rule has two implementations, something
has to hold them together or one of them becomes a suggestion.
"""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = ROOT / "pyproject.toml"
REQUIREMENTS = ROOT / "backend" / "requirements.txt"


def name_of(spec: str) -> str:
    """The package name out of a requirement, without version or extras."""

    return re.split(r"[<>=!\[;]", spec.strip(), maxsplit=1)[0].strip().lower()


def requirements() -> set[str]:
    lines = REQUIREMENTS.read_text(encoding="utf-8").splitlines()
    return {
        name_of(line)
        for line in lines
        if line.strip() and not line.lstrip().startswith("#")
    }


def declared() -> tuple[set[str], set[str]]:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    project = data["project"]
    runtime = {name_of(d) for d in project["dependencies"]}
    dev = {name_of(d) for d in project.get("optional-dependencies", {}).get("dev", [])}
    return runtime, dev


def test_the_two_dependency_lists_agree():
    runtime, dev = declared()
    missing = requirements() - (runtime | dev)
    extra = (runtime | dev) - requirements()

    assert not missing, f"in requirements.txt but not pyproject.toml: {sorted(missing)}"
    assert not extra, f"in pyproject.toml but not requirements.txt: {sorted(extra)}"


def test_the_deployment_does_not_ship_the_test_tools():
    """pytest in a serverless bundle is dead weight on every cold start."""

    runtime, _ = declared()
    assert "pytest" not in runtime


def test_blob_storage_is_a_real_dependency():
    """Sharing silently stops working when deployed if this is ever dropped."""

    runtime, _ = declared()
    assert "vercel" in runtime


def test_vercel_is_pointed_at_the_right_entrypoint():
    """A wrong entrypoint is a deploy that succeeds and serves nothing."""

    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    assert data["tool"]["vercel"]["entrypoint"] == "index:app"
    assert (ROOT / "index.py").is_file()
