"""Validate the standard HACS layout and synchronized release version locally."""

import argparse
import json
from pathlib import Path
import re
import tomllib

ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)")


def validate(root: Path = ROOT, tag: str | None = None) -> str:
    """Reject mismatched tags/versions and incorrect HACS download layout."""
    integration = root / "custom_components/tmd_weather"
    manifest = json.loads((integration / "manifest.json").read_text())
    hacs = json.loads((root / "hacs.json").read_text())
    project = tomllib.loads((root / "pyproject.toml").read_text())
    version = manifest["version"]
    if not isinstance(version, str) or SEMVER.fullmatch(version) is None:
        raise ValueError("Manifest version must be a semantic x.y.z version")
    if project["project"]["version"] != version:
        raise ValueError("Project and integration versions differ")
    if tag is not None and tag != f"v{version}":
        raise ValueError("Release tag must be v followed by the manifest version")
    if f"## {version} " not in (root / "CHANGELOG.md").read_text():
        raise ValueError("Changelog does not describe this version")
    domains = {
        path.name
        for path in (root / "custom_components").iterdir()
        if path.is_dir() and not path.name.startswith((".", "__"))
    }
    if domains != {manifest["domain"]} or manifest["domain"] != "tmd_weather":
        raise ValueError("HACS must find exactly one tmd_weather integration")
    if hacs.get("content_in_root", False) or hacs.get("zip_release", False):
        raise ValueError(
            "Use the standard integration directory, without release archives"
        )
    if hacs["homeassistant"] != "2026.10.1":
        raise ValueError(
            "HACS minimum HA version must match the tested supported runtime"
        )
    for path in [
        root / "README.md",
        root / "LICENSE",
        integration / "__init__.py",
        integration / "brand/icon.png",
        integration / "translations/en.json",
        integration / "translations/th.json",
    ]:
        if not path.is_file():
            raise ValueError(f"Missing packaging file: {path.relative_to(root)}")
    return version


def main() -> None:
    """Validate a checkout or proposed release; never create a tag or publish."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tag", help="Require a release tag matching v<manifest version>"
    )
    args = parser.parse_args()
    try:
        version = validate(tag=args.tag)
    except (ValueError, KeyError, OSError) as error:
        parser.exit(1, f"Packaging validation failed: {error}\n")
    print(f"HACS layout and version {version} validated")


if __name__ == "__main__":
    main()
