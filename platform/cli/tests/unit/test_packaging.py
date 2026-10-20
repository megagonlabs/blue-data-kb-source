"""Every file the CLI loads at runtime is declared as package data, so the wheel
carries it: profile_spec.json was left out and every command failed outside an
editable install (#146)."""
import fnmatch
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # 3.10: pytest depends on the tomli backport
    import tomli as tomllib

CLI_ROOT = Path(__file__).resolve().parents[2]
PACKAGE = CLI_ROOT / "src" / "blue_data_kb_cli"


def test_every_non_python_file_is_package_data():
    config = tomllib.loads((CLI_ROOT / "pyproject.toml").read_text())
    patterns = config["tool"]["setuptools"]["package-data"]["blue_data_kb_cli"]
    data_files = [p.name for p in PACKAGE.iterdir()
                  if p.is_file() and p.suffix not in (".py", ".pyc")]
    assert "profile_spec.json" in data_files
    missing = [f for f in data_files if not any(fnmatch.fnmatch(f, pat) for pat in patterns)]
    assert not missing, f"not declared as package data, so not in the wheel: {missing}"
