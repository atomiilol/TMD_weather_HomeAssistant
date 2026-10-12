# Releases and repository metadata

The initial version is **0.1.0**, prepared for review. Nothing in this task creates
or pushes a Git tag or publishes a GitHub Release.

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

The GitHub API currently confirms that this repository has no topics. Reading
metadata works, but the integration credential cannot update topics: GitHub
returns `403: Resource not accessible by integration`. Repository write access
for Git pushes does not establish permission to edit repository settings. A
repository owner or another suitably authorized user must add the topics through
GitHub or grant an API credential the required permission. Recommitting files
cannot change this metadata, and the validation check should remain enabled.

## Publish a version

1. Update `custom_components/tmd_weather/manifest.json` and `pyproject.toml` to
   the same semantic `x.y.z` version, and add a `## x.y.z — ...` changelog entry.
   When project metadata changes, refresh `uv.lock` with `uv lock` and review it.
2. Set the release's tested minimum HA version in `hacs.json` if compatibility
   changes; update the README and packaging check alongside that policy.
3. Run the tests/lint/types from the README, plus:

   ```sh
   uv run --frozen python script/check_release.py --tag v0.1.0
   ```

   Substitute the proposed version for later releases. The check rejects a tag
   mismatch, an invalid semantic version, missing changelog/assets, or a layout
   that would make HACS install the wrong directory.
4. Commit and push the reviewed changes. Wait for **Tests**, **HACS validation**
   and **Hassfest validation** to pass on GitHub; fix actual failures.
5. In GitHub's **Releases**, create and publish a release with tag `v0.1.0`
   (or the matching later version) pointing to that tested commit. Copy the
   version's changelog into the release notes. Use GitHub's pre-release flag for
   releases intended only for testing.
6. The tag-push workflow also checks tag/manifest/project/changelog agreement.
   Verify its result before announcing the release. A Git tag by itself does
   not provide the published-release version that HACS users expect.
7. Check the repository in HACS with the intended HA version. Install it, restart,
   configure through the UI, then verify an upgrade preserves config entries,
   device IDs and entity IDs before publishing the next release.

Do not attach a custom ZIP or package the repository's test/development files.
HACS supports this standard `custom_components/tmd_weather/` layout directly.
Leave `zip_release` unset. GitHub's automatic source archives are sufficient;
HACS identifies the integration content inside the chosen ref.

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
