# ArgonSDK-Tooling

Shared, **portable** cook/pak and mod-pack tooling for the Chivalry 2 SDK.
This is a UE **plugin** (`ArgonSDKTooling`) so a single copy of the code and
widgets serves **both** SDK projects:

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
Content/
  Python/                 # auto on the editor's Python path (PythonScriptPlugin)
    deploy.py             # entry point: cook / pak / cook+pak / sync-tooling
    deploy/               # module package
  Scripts/
    ModHandling/          # stock widget .uasset (replaceable, overwritten on update)
    _Local/               # user customizations (git-ignored, never overwritten)
```

## Using in a SDK

Drop (or submodule) `ArgonSDKTooling/` into `<SDK>/Plugins/`, enable
`ArgonSDKTooling` in `TBL.uproject`, and restart the editor.

```bash
# one-line clone (from a machine with the SDK)
git clone --recurse-submodules <sdk-url>
# update tooling only (replace widgets/scripts, preserve _Local)
python deploy.py sync-tooling
```

## Portability

No hardcoded paths. The project is found from the script's own location; the
engine is resolved from the `.uproject`'s `EngineAssociation` the same way
Unreal does (registry / `%ProgramData%` / `%APPDATA%\\UnrealEngine\\Install.ini`),
with a per-user git-ignored `deploy.local.json` + `DEPLOY_*` env fallback.

See the epic `sdk_tweaks-vje` for the work breakdown.
