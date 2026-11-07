# ArgonSDK-Tooling

Shared, **portable** cook/pak and mod-pack tooling for the Chivalry 2 SDK.
This is a UE **plugin** (`ArgonSDKTooling`) so a single copy of the **code**
serves **both** SDK projects (the widgets move in here too under vje.9; see
`Content/Scripts/ModHandling/`):

- `ArgonSDK` — UE 4.25 (Launcher engine, `EngineAssociation: "4.25"`)
- `Unchained` — UE 4.25+ (Plus source engine, `EngineAssociation: "{...GUID}"`)

## Why this exists / the update model

The old setup split this tooling across every SDK's `Content/Python` and
`Content/Scripts/ModHandling`, which:
- duplicated the cook script per SDK (single `cook_mod.py`, drifting copies),
- worked only for one machine's hardcoded paths,
- made the widgets (binary `.uasset`, **unmergeable**) a `git pull` target —
  anyone with local widget edits could not cleanly update.

That last point is the core design rule here:

> **Code/scripts are version-controlled text (diff/merge fine).**
> **Widgets (`.uasset`) are *replaceable artifacts*, updated by OVERWRITE, never merged.**

Update is therefore always *replace-not-merge* (see `deploy.py sync-tooling`),
and any user customization lives in `Content/Scripts/_Local/` which updates
**never touch**.

## Layout

```
ArgonSDKTooling.uplugin
AGENTS.md
tests/                  # stdlib unittest, CLI-runnable (python3 tests/*.py)
Content/
  Python/                 # auto on the editor's Python path (PythonScriptPlugin)
    deploy.py             # CLI shim -> deploy._main (cook / pak / sync-tooling)
    deploy/               # module package; `import deploy` = THIS (the API)
    modtools.py           # editor-only: mod / marker creation helpers
  Scripts/
    ModHandling/          # widget home (stock .uasset added in vje.9; dir currently README-only)
    _Local/               # user customizations (git-ignored, never overwritten)
```
Git-ignored per-user `deploy.local.json` (next to the plugin root or CWD) + `DEPLOY_*` env hold path/size overrides; never committed.

## Using in a SDK

Drop (or submodule) `ArgonSDKTooling/` into `<SDK>/Plugins/`, enable
`ArgonSDKTooling` in `TBL.uproject`, and restart the editor.

```bash
# one-line clone (from a machine with the SDK)
git clone --recurse-submodules <sdk-url>
# update tooling only (replace widgets/scripts, preserve _Local)
python deploy.py sync-tooling
```
> `sync-tooling` needs a source: pass `--src <dir|git-url>`, or set
> `DEPLOY_TOOLING_REPO` / `tooling_repo` in `deploy.local.json`, or install the
> plugin as a git clone. Without one it exits with a "no source" error.

## Editor usage (in the widget)

The plugin's primary consumers are the SDK's in-editor widgets, which use the
**package** API via `import deploy` (note: `deploy.py` is only a CLI shim —
`import deploy` resolves to `deploy/__init__.py`):

```python
import deploy
deploy.run_from_widget(mod_name, mod_dir, upr, engine, dest, do_cook, do_pak)  # single
deploy.start_many([...mods...], dest_dir=..., do_cook=..., do_pak=...)          # multi, non-blocking
deploy.cook_mod(...) / deploy.cook_and_pak(...)                                 # blocking

# widget Event Tick -> read state
s = deploy.poll()          # state: idle/cooking/paking/done; mod; index/total;
                           # when done: success/result/error
```

## Portability

No hardcoded paths. The project is found from the script's own location; the
engine is resolved from the `.uproject`'s `EngineAssociation` the same way
Unreal does (registry / `%ProgramData%` / `%APPDATA%\\UnrealEngine\\Install.ini`),
with a per-user git-ignored `deploy.local.json` + `DEPLOY_*` env fallback.

See the epic `sdk_tweaks-vje` for the work breakdown.
