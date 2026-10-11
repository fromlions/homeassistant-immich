# AGENTS.md

A Home Assistant custom integration that shows random pictures from the owner's Immich as `image` entities.
A public fork of outadoc/immich-home-assistant (v0.2.1, unmaintained since June 2024), kept as a fork so the
original author keeps the credit: never detach it from its upstream, never remove the notice at the top of the
README, never add a licence of our own over upstream's code (upstream has none). Installed on the Home Assistant VM
through HACS as a custom repository.

## Layout

- `custom_components/immich/`: the integration, exactly what is copied to `/config/custom_components/immich/`.
- Nothing else is deployed. Tooling and CI config live at the root.

## Working rules

- Every design point is approved by the user before it is written down. Propose, then wait.
- Claims about Home Assistant, Immich or third-party tools are verified against upstream source or docs before
  they become a decision.
- Don't repeat yourself. Code or text needed in more than one place lives in one place that the others use.
- Comments say why, never dated incident stories or measurements. Git history and PR text hold the story.

## Git

- Every change goes through a feature branch and a pull request. Never push to `main`.
- Every pull request targets `main`. No stacked PRs.
- Never merge a pull request; the user merges.
- Stage named paths, never `git add -A` at the repo root.
- Commits end with the attribution line given by the session; pull request bodies likewise.

## Code

- The component runs inside Home Assistant (Python 3.13, the HA core's libraries: `aiohttp`, `voluptuous`,
  `homeassistant.*`). Add no dependency without listing it in `manifest.json` `requirements`.
- Target the Immich API version the owner runs; check a change against Immich's own API docs/OpenAPI before
  relying on a field or type. Immich validates request types: send JSON with real booleans and numbers.
- Home Assistant moves fast: a deprecation warning in its log is a bug to fix now, not later.
- Tooling: `ruff check .`, `python -m compileall -q custom_components` and `python -m unittest discover -s tests -t .`
  must pass (CI runs all three; the tests need `aiohttp` only: `tests/ha_stubs.py` stands in for Home Assistant, so
  keep it in step with the Home Assistant names the component imports). Run `pre-commit install` once.

## Deploying

In HACS: Custom repositories → `fromlions/homeassistant-immich`, category Integration, then download it and
restart Home Assistant. Upstream's own HACS entry must not stay installed next to it: its update would install the
unmaintained upstream version. A change here reaches Home Assistant when HACS offers the update (create a release
or tag for a clean version number) and the owner installs it.
