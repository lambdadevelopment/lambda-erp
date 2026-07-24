"""Lambda ERP - Core ERP logic packaged as a standalone library."""


def get_app_version() -> str:
    """Runtime version of the ERP core.

    The single human-edited source is the packaging version in `pyproject.toml`,
    which the release process bumps. A wheel/pip install doesn't ship
    `pyproject.toml`, so there we read the installed package metadata (populated
    from that same version at build time). A source checkout (incl. editable
    dev) reads `pyproject.toml` directly, so it's correct without a reinstall.
    Falls back to "dev" when neither is available.
    """
    import re
    from pathlib import Path

    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    if pyproject.is_file():
        match = re.search(
            r'^version\s*=\s*"([^"]+)"', pyproject.read_text(encoding="utf-8"), re.MULTILINE
        )
        if match:
            return match.group(1)

    try:
        from importlib.metadata import version

        return version("lambda-erp")
    except Exception:
        return "dev"


__version__ = get_app_version()
