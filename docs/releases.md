# Releases and repository metadata

The release version is **0.2.0**. Published versions are listed in
[GitHub Releases](https://github.com/atomiilol/TMD_weather_HomeAssistant/releases).
HACS uses the latest published release tag as the available update version.

## GitHub repository settings

HACS requires a public GitHub repository with a description, topics, README,
issues enabled and an unarchived state. For
`atomiilol/TMD_weather_HomeAssistant`, set these in the repository's **About** /
**Settings** UI before the first published HACS release:

- Description: **Home Assistant weather integration using official Thai
  Meteorological Department station observations and NWP forecasts.**
- Topics: `home-assistant`, `hacs`, `custom-integration`, `weather`, `thailand`, `tmd`.
- Visibility: public; issues enabled; repository not archived.

These are GitHub settings, not fields a local JSON file or Git commit can apply.
The HACS workflow checks them without ignoring failures.

### Fixing the HACS topics check

If HACS reports `The repository has no valid topics`, open the repository's
**About** section, select its gear icon, and add `weather`, `thailand`, and `tmd`
as topics. Save, then rerun the failed HACS workflow from GitHub Actions.
HACS filters generic topics such as `home-assistant` and `hacs`; those labels alone
do not satisfy the check. Keep descriptive topics as well.

This repository now has `weather`, `thailand` and `tmd` topics. Earlier validation
failed while its topics were empty, and the environment's integration credential
received `403: Resource not accessible by integration` when attempting to edit
them. Repository write access for Git pushes does not establish permission to
edit repository settings. Future topic changes require a repository owner or
another suitably authorized user. Recommitting files cannot change this metadata,
and the validation check should remain enabled.

## Publish a version

1. Update `custom_components/tmd_weather/manifest.json` and `pyproject.toml` to
   the same semantic `x.y.z` version, and add a `## x.y.z — ...` changelog entry.
   When project metadata changes, refresh `uv.lock` with `uv lock` and review it.
2. Set the release's tested minimum HA version in `hacs.json` if compatibility
   changes; update the README and packaging check alongside that policy.
3. Run the tests/lint/types from the README, plus:

   ```sh
   uv run --frozen python script/check_release.py --tag v0.2.0
   ```

   Substitute the proposed version for later releases. The check rejects a tag
   mismatch, an invalid semantic version, missing changelog/assets, or a layout
   that would make HACS install the wrong directory.
4. Commit and push the reviewed changes. Wait for **Tests**, **HACS validation**
   and **Hassfest validation** to pass on GitHub; fix actual failures.
5. Create an annotated version tag on the tested commit, then push that tag:

   ```sh
   git tag -a v0.2.0 -m "Release v0.2.0" <tested-commit-sha>
   git push origin v0.2.0
   ```

   Use the next semantic version for subsequent releases; never move or reuse
   a published version tag.
6. The **Publish GitHub release** workflow checks tag/manifest/project/changelog
   agreement and runs the reusable tests, HACS and Hassfest workflows on the
   tagged commit. Only if all succeed does it publish a non-draft, non-prerelease
   GitHub release, mark it latest and use that version's changelog as the notes.
   Only the publishing job receives `contents: write`; validation stays read-only.
   Confirm the workflow and published release before announcing the update.
   A Git tag by itself does not provide the release version HACS expects.
7. Check the repository in HACS with the intended HA version. Install it, restart,
   configure through the UI, then verify an upgrade preserves config entries,
   device IDs and entity IDs before publishing the next release.

Do not attach a custom ZIP or package the repository's test/development files.
HACS supports this standard `custom_components/tmd_weather/` layout directly.
Leave `zip_release` unset. GitHub's automatic source archives are sufficient;
HACS identifies the integration content inside the chosen ref.

If a publishing run fails, fix the cause and rerun it from GitHub Actions. For an
existing tag whose release has not been published, **Run workflow** with that tag
selected, or use `gh workflow run release.yml --ref v0.2.0`. Dispatching on a
branch is rejected. An existing published release is left intact during retries.
Do not overwrite a published release to distribute changes: create a new version.

The release workflow needs GitHub Actions enabled and permission for its
`GITHUB_TOKEN` publishing job to write repository contents. If repository or
organization policy prevents that, an authorized maintainer can publish the
validated tag manually in GitHub's **Releases** using the matching changelog notes.

## Updating users safely

Publish a new release for each distributed version; keep its `v`-prefixed tag
matched to the numeric manifest version. HACS regularly checks GitHub and creates
normal update notifications. Users install the update and restart HA; they do
not delete the integration or re-enter existing credentials for ordinary upgrades.

The integration stores runtime state in `entry.runtime_data` and configuration
in HA's config entries. Coordinate unique IDs and the existing rainfall sensor
ID are preserved when enabling/disabling station observations. Optional observation
credentials are additive; existing version-1 modeled entries continue to load.
If a future release changes stored data incompatibly, increment config-entry
schema version and implement/test `async_migrate_entry` before that release.

Custom packaging is limited to the manifest version, `hacs.json`, brand files,
documentation and workflows. The Python integration contains no HACS-specific
startup/update logic and behaves the same when manually installed.
