# Contributing to kde-build-meta

**This repository is archived, historical reference only.** As
[README.md](README.md) explains, the KDE/Plasma/freedesktop-sdk BuildStream
elements that used to live here were consolidated into
[tuna-os/tromso](https://github.com/tuna-os/tromso), which no longer
consumes this repo through a junction. **New contributions, source updates,
and build changes belong in Tromso**, not here — see
[issue #13](https://github.com/tuna-os/kde-build-meta/issues/13) for the
retirement rationale.

## What this repo is still useful for

- Archaeology: understanding how the pre-consolidation KDE Linux BuildStream
  project (`elements/`, `patches/`, `plugins/`) was structured, modeled on
  GNOME's [gnome-build-meta](https://gitlab.gnome.org/GNOME/gnome-build-meta).
- Archaeology for the OpenQA end-to-end test harness under `tests/openqa/`,
  which has
  [its own historical contributing guide](tests/openqa/CONTRIBUTING.md).

## Verifying historical content locally

This repository has no active GitHub Actions workflow. The retained
`.gitlab-ci.yml` and `.gitlab-ci/` files document the former upstream pipeline;
GitHub does not execute them. Nothing in this repository is therefore enforced
by CI.

For local inspection of the historical tree:

- BuildStream elements are driven through `just` (see the [Justfile](Justfile)):
  `just bst show <target>.bst` to inspect the dependency graph, `just bst-build`
  to build. Both run `bst` inside the pinned `bst2` container image via Podman.
- Markdown can be linted locally with the configuration in
  [`.rumdl.toml`](.rumdl.toml).
- `plugins/*.py` and `utils/*.py` are the only Python in this tree (small
  BuildStream element plugins and maintenance scripts); lint them with
  `ruff check plugins utils` using [`ruff.toml`](ruff.toml).
Changes to shipped BuildStream elements, Python tools, or OpenQA coverage belong
in Tromso rather than this snapshot.

## Project docs

- [README.md](README.md) — current status and where to go instead.
- `docs/` — historical install/debugging notes for the phone targets
  (`FP5.md`, `ONEPLUS6.md`, `install.md`, `debugging.md`, `using.md`).
