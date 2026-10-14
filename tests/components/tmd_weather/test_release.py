"""Guard HACS layout, local brand assets, and semantic release consistency."""

import json
import shutil

from PIL import Image
import pytest

from script.check_release import ROOT, main, release_notes, validate


def test_release_and_hacs_layout():
    """The proposed release has one integration and uses standard HACS refs."""
    assert validate(tag="v0.5.0") == "0.5.0"
    hacs = json.loads((ROOT / "hacs.json").read_text())
    assert hacs["name"] == "Thai Meteorological Department (TMD)"
    assert hacs["country"] == "TH"
    assert not hacs.get("zip_release", False)
    assert "render_readme" not in hacs


@pytest.mark.parametrize("tag", ["0.2.0", "v1.0.0", "v0.2.0-dev", "latest"])
def test_wrong_release_tag(tag):
    """Reject tags that would disagree with the version installed by HA."""
    with pytest.raises(ValueError, match="Release tag"):
        validate(tag=tag)


@pytest.fixture
def release_copy(tmp_path):
    """Copy only release metadata and integration files for mutation tests."""
    for name in ("pyproject.toml", "README.md", "LICENSE", "CHANGELOG.md", "hacs.json"):
        shutil.copyfile(ROOT / name, tmp_path / name)
    shutil.copytree(
        ROOT / "custom_components",
        tmp_path / "custom_components",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    return tmp_path


def test_project_version_mismatch(release_copy):
    """Do not distribute divergent project and integration versions."""
    path = release_copy / "pyproject.toml"
    path.write_text(path.read_text().replace('version = "0.5.0"', 'version = "0.5.1"'))
    with pytest.raises(ValueError, match="versions differ"):
        validate(release_copy)


def test_extra_integration(release_copy):
    """Prevent HACS's first-directory discovery from selecting an unrelated domain."""
    (release_copy / "custom_components/another").mkdir()
    with pytest.raises(ValueError, match="exactly one"):
        validate(release_copy)


def test_missing_brand(release_copy):
    """The release must include the local icon required by current HACS checks."""
    (release_copy / "custom_components/tmd_weather/brand/icon.png").unlink()
    with pytest.raises(ValueError, match="Missing packaging"):
        validate(release_copy)


def test_missing_aws_catalog(release_copy):
    """A HACS source install must include the catalog with the AWS implementation."""
    (release_copy / "custom_components/tmd_weather/data/tmd_aws_stations.json").unlink()
    with pytest.raises(ValueError, match="AWS station database"):
        validate(release_copy)


def test_stable_workflow_metadata(tmp_path, monkeypatch):
    """The validated stable release uses the publisher's latest-release slot."""
    output = tmp_path / "github-output"
    monkeypatch.setattr(
        "sys.argv",
        ["check_release.py", "--tag", "v0.5.0", "--github-output", str(output)],
    )
    main()
    assert output.read_text() == "version=0.5.0\nprerelease=false\n"


def test_beta_versions_still_valid(release_copy):
    """A later beta keeps the prerelease version conventions after promotion."""
    for name in (
        "custom_components/tmd_weather/manifest.json",
        "pyproject.toml",
        "CHANGELOG.md",
    ):
        path = release_copy / name
        path.write_text(path.read_text().replace("0.5.0", "0.6.0b1"))
    assert validate(release_copy, tag="v0.6.0b1") == "0.6.0b1"


def test_release_notes(release_copy):
    """Publish only current notes, independent of earlier release descriptions."""
    version = validate(release_copy)
    (release_copy / "CHANGELOG.md").write_text(
        f"# Changelog\n\n## {version} — today\n\nCurrent release changes.\n"
        "\nRequires Home Assistant 2026.10.1.\n\n## 0.0.1 — earlier\n\nOld changes.\n"
    )
    notes = release_notes(release_copy)
    assert notes == "Current release changes.\n\nRequires Home Assistant 2026.10.1.\n"
    assert "Old changes" not in notes


def test_empty_release_notes(release_copy):
    """Do not publish a release with a heading but no useful notes."""
    (release_copy / "CHANGELOG.md").write_text(
        f"# Changelog\n\n## {validate(release_copy)} — today\n"
    )
    with pytest.raises(ValueError, match="must not be empty"):
        release_notes(release_copy)


@pytest.mark.parametrize(
    "filename", ["icon.png", "icon@2x.png", "logo.png", "logo@2x.png"]
)
def test_brand_images(filename):
    """Keep the supplied high-resolution emblem intact in all brand variants."""
    with Image.open(ROOT / "custom_components/tmd_weather/brand" / filename) as image:
        assert image.format == "PNG"
        assert image.size == (2340, 2404)
        assert image.mode in ("RGB", "RGBA")
        assert image.getbbox() is not None
