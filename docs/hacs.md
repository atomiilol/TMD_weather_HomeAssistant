# HACS compatibility and validation

Reviewed the current official HACS documentation and implementation on 2026-10-12.
The rendered documentation site was blocked in this environment, so its official
[GitHub source](https://github.com/hacs/documentation) was inspected directly.

## Current supported packaging

- One integration under `custom_components/tmd_weather/`, containing all runtime
  files, translations and local `brand/icon.png` assets.
- Integration manifest has domain, name, codeowners, documentation, issue tracker
  and a semantic custom-integration version, plus current HA config-flow/service
  metadata. Runtime requirements contain no HACS dependency.
- Root `hacs.json` declares name, Thailand and minimum HA `2026.10.1`.
- No `content_in_root`, `zip_release`, `filename` or old custom README-rendering
  option is needed. Current HACS renders the repository README automatically.
- HACS installs files directly from the integration directory at a release/tag
  or the default branch. Tests, development dependencies and root documentation
  are outside that directory and are not part of the installed integration.
- Published GitHub Releases, not tags alone, provide release versions and normal
  update notifications. Minimum HA compatibility comes from `hacs.json`.

Brand images use the user-supplied TMD emblem, preserved without altering the
artwork or its white background. The original high-resolution PNG is included as
`icon.png`, `icon@2x.png`, `logo.png` and `logo@2x.png` so Home Assistant can serve
both icon and logo requests locally. No Home Assistant brands-repository submission
is needed for local custom-integration assets.

## Official references inspected

- [Integration publishing requirements](https://www.hacs.xyz/docs/publish/integration/)
  ([source](https://github.com/hacs/documentation/blob/main/source/docs/publish/integration.md)).
- [General requirements / hacs.json / releases](https://www.hacs.xyz/docs/publish/start/)
  ([source](https://github.com/hacs/documentation/blob/main/source/docs/publish/start.md)).
- [Custom repositories UI](https://www.hacs.xyz/docs/faq/custom_repositories/)
  ([source](https://github.com/hacs/documentation/blob/main/source/docs/faq/custom_repositories.md)).
- [Downloading repositories](https://www.hacs.xyz/docs/use/repositories/dashboard/)
  and [updates](https://www.hacs.xyz/docs/use/update/).
- [Supported My links](https://www.hacs.xyz/docs/use/my/), plus the official
  [redirect definition](https://github.com/home-assistant/my.home-assistant.io/blob/main/redirect.json)
  and badge asset. The link uses `owner`, `repository`, and `category=integration`;
  it opens HACS, where the user confirms download.
- [HACS action](https://www.hacs.xyz/docs/publish/action/) and its current
  [action definition](https://github.com/hacs/action/blob/main/action.yml).
- [Current HACS validation schemas](https://github.com/hacs/integration/blob/main/custom_components/hacs/utils/validate.py),
  integration-directory discovery, and local-brand validator.
- [Official hassfest action](https://github.com/home-assistant/actions/tree/master/hassfest)
  and standalone validator container. No validation checks are suppressed.
- [HA local brand images](https://developers.home-assistant.io/docs/creating_integration_file_structure/#brand-images---brand).

The workflow references `hacs/action@main` and
`home-assistant/actions/hassfest@master` as documented by the official actions.
GitHub checkout/setup-python use verified commit pins. Dependabot tracks action
updates. HACS PR comments are disabled so validation needs only
read permissions.

## Validation scope

Local checks exercise HACS's current official JSON schemas and its manifest,
README-presence and local-brand validators against this checkout. The official
hassfest container validates all standalone integration plugins. Lint, typing,
packaging/version checks and mocked Home Assistant tests also run locally.

An earlier HACS action reported eight of nine checks passing because repository
topics were missing. GitHub now confirms the descriptive topics `weather`,
`thailand` and `tmd` are present. The environment's credential previously received
`403: Resource not accessible by integration` when editing topics; Git push
permission does not include repository-settings access. See the owner-side
procedure in [releases.md](releases.md#fixing-the-hacs-topics-check).

CI is configured for HACS, hassfest, tests and tag/version agreement. Review the
workflow results for the commit being released. Version tags run those validations
before publishing GitHub releases used by HACS for update detection. Actual HACS
installation and
update testing against a published repository have not been performed here.
