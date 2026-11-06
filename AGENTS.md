# Agent Instructions

## Commit convention (owner rule, 2026-11-06)

**Do NOT prefix git commit subjects with a beads ticket id.** Do not write
subjects like `sdk_tweaks-vje.13: ...`. Use a plain, descriptive subject (e.g.
`pak: add per-size splitting`). A ticket reference is fine in the body if
helpful, but never in the subject. This overrides the shared `close-ticket`
skill, whose "commit with the ticket id" convention does not apply to this repo.

Keep commits to identity `Knutschbert <knutschbert@gmail.com>`, local-only
(no push) unless the owner asks.

## What this is

`ArgonSDKTooling` - a UE plugin shared across the Chivalry 2 SDKs. Carries the
portable cook/pak tooling (`Content/Python/deploy`), `modtools` (marker/actor
creation incl. V2 markers), and is the widget home (`Content/Scripts/ModHandling`,
with `_Local/` as a git-ignored user-customization overlay, never overwritten by
`deploy.py sync-tooling`).

Part of the `sdk_tweaks` tracking epic `sdk_tweaks-vje`.

## Portability / constraints

- No hardcoded paths; the engine/project resolve from the project
  (`EngineAssociation`), config (`deploy.local.json`, git-ignored), and env.
- Python 2.7-safe (also runs in the UE editor's Python) AND CLI-runnable.
- Editor-only pieces (anything touching `unreal`): `modtools.py`, parts of
  `deploy/version.py`. These cannot be unit-tested on the CLI; mark gated work
  explicitly and verify in the editor.
- Widgets are replaceable artifacts: updates are overwrite-not-merge.

## Build / test

```bash
# from the repo root (no editor required)
python3 tests/test_engine.py tests/test_unrealpak.py tests/test_cook.py \
        tests/test_provenance.py tests/test_pak.py tests/test_sync.py \
        tests/test_version.py
# or individual:
python3 tests/test_pak.py
```

`deploy.py --help` and `deploy.py cook <mod> --dry-run` work on any Python
(no editor/game needed).

## Tracker

Use `bd`/beads in the `sdk_tweaks` repo (`/home/rmary/projects/unchained/sdk_tweaks`).
This repo's work is tracked under epic `sdk_tweaks-vje`. Use `bd` for durable
task tracking; do not create ad-hoc TODO files.
