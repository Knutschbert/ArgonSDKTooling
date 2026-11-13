# ArgonSDK-Tooling

Shared, **portable** cook/pak and mod-pack tooling for the Chivalry 2 SDK.
This is a UE **plugin** (`ArgonSDKTooling`) so a single copy of the **code**
serves **both** SDK projects (the editor widgets live here too, in
`Content/Scripts/ModHandling/`):

- `ArgonSDK`: UE 4.25 (Launcher engine, `EngineAssociation: "4.25"`)
- `Unchained`: UE 4.25+ (Plus source engine, `EngineAssociation: "{...GUID}"`)

> [!TIP]
> **New to packaging mods? Start with the [Mod Packer Usage Guide](./USAGE.md).** It is a
> step-by-step walkthrough of the in-editor widget: creating mods, cooking, paking, and the
> mod/map markers. This README is the reference for the CLI, project setup, and the internals.

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

## Project setup (ini config, one-time per SDK)

Enabling the plugin is not enough: two project config files need entries before
the marker workflow and the cook behave correctly. Paths below assume the stock
`Content/Mods/ArgonSDK/Mods/ModLoading` layout.

### 1. Load the marker scripts: `Config/DefaultEngine.ini`

The map/mod marker right-click and toolbar actions come from the SDK's
`ModLoading/Python` scripts; they only register if the editor runs them at
startup. Under `[/Script/PythonScriptPlugin.PythonScriptPluginSettings]`:

```ini
+AdditionalPaths=(Path="../../../TBL/Content/Python")
+AdditionalPaths=(Path="../../../TBL/Content/Mods/ArgonSDK/Mods/ModLoading/Python")
+StartupScripts=import sys, unreal; p = unreal.Paths.project_content_dir() + 'Mods/ArgonSDK/Mods/ModLoading/Python'; sys.path.append(p) if p not in sys.path else None; import mapmarker_menu; mapmarker_menu.register()
bDeveloperMode=True
```

- **`DefaultEngine.ini` is UTF-16 (with a BOM).** Edit it as UTF-16, or Unreal
  silently ignores your changes.
- The **`+StartupScripts` line is what actually registers the menus**: it appends
  the scripts folder to `sys.path` via `project_content_dir()` and calls
  `mapmarker_menu.register()`. `+AdditionalPaths` on its own is **not** enough:
  those are resolved relative to `Binaries/Win64` and do not reach the content
  folder here, so the explicit `sys.path` append is required.
- Restart the editor after changing this.

> The `Unchained` SDK already has these lines; the clean `ArgonSDK` checkout does
> not yet, so add them there when you wire the marker scripts in.

### 2. Keep Blueprint structs/enums shippable: `Config/DefaultGame.ini`

The V2 map marker stores its rows in a Blueprint struct (`F_ShippedMap`) and enum
(`E_MapShipState`). The base project packages with **exclusive** Blueprint
nativization:

```ini
[/Script/UnrealEd.ProjectPackagingSettings]
BlueprintNativizationMethod=Exclusive
```

Under nativization, a converted type becomes generated C++ with **no shipped
`.uasset`**. In UE 4.25 that can pull a mod's user-defined structs/enums into
nativization and drop their assets from the pak, even though the mod's Blueprints
still import them, so the mod fails to load. Leave this value as the base game
sets it (`Exclusive`); the cooker then **auto-detects the user structs/enums a mod
uses and excludes them from nativization** for that cook, so they ship as assets
(recorded in the manifest's `nativization_exclusions`). No per-mod config is
needed in the editor. In the CLI/headless path the editor Asset Registry is
unavailable, so pass them explicitly or the cook fails closed:

```bash
python deploy.py cook <mod> --exclude-asset /Game/.../F_ShippedMap --exclude-asset /Game/.../E_MapShipState
```

### Optional: docked widget

A docked Mod Packer widget and Content Browser favorites can be shipped via
`Config/DefaultEditorPerProjectUserSettings.ini` (`LoadedUIs` / `FavoritePaths`).
This is convenience only; the tooling works without it.

## Editor usage (in the widget)

The plugin's primary consumers are the SDK's in-editor widgets, which use the
**package** API via `import deploy` (note: `deploy.py` is only a CLI shim;
`import deploy` resolves to `deploy/__init__.py`):

```python
import deploy
deploy.run_from_widget(mod_name, mod_dir, upr, engine, dest, do_cook, do_pak)  # single
deploy.start_many([...mods...], dest_dir=..., do_cook=..., do_pak=...)          # multi, non-blocking
deploy.cook_mod(...) / deploy.cook_and_pak(...)                                 # blocking

# cook_many / pak_many also run non-blocking (same poll() UI as a single marker):
deploy.cook_many([...mods...], dest_dir=..., non_blocking=True)   # cook (+pak if dest) async
deploy.pak_many([...mods...], dest, non_blocking=True)            # pak already-cooked output async

# After a fresh cook, paking skips the redundant source re-validation and the
# separate UnrealPak -List pass (the .txt is written locally instead). Keep those
# for pure pak-only runs, which still fully validate provenance by default.
deploy.pak_mod(result, dest, run_list=False)   # fast: no second UnrealPak spawn
deploy.pak_mod(result, dest, run_list=True)    # also run UnrealPak -List to validate contents
# When the cook emitted TBL/AssetRegistry.bin, pak_mod also includes it as
# TBL/Content/<mod-path>/ModAssetRegistry.bin (never project-root Content/AssetRegistry.bin).

# widget Event Tick -> read state
s = deploy.poll()          # state: idle/cooking/paking/done/error; mod; index/total;
                           # when done: success/result/error
```

Widget notes (learned the hard way):
- `poll()` sets `index = total` once a run is `done`, so a progress label `index+1 / total` reads `2/1` at the end; use
  `min(index + 1, total)`.
- Do not reset the UI with `SetTimerByEvent` from an Editor Utility Widget: the editor world's timer manager only ticks while
  an editor viewport is realtime (`UEditorEngine::Tick` -> `LEVELTICK_TimeOnly` otherwise), so the timer may never fire. Count
  down in the widget's tick instead.
- `_tick` (the Slate post-tick that drives a run) is not re-entrant: `start_cook` can save assets (marker version stamping),
  which pumps Slate and ticks again before the first job is registered. A nested tick used to start the same cook twice and
  fail the second pak with "a pak is already running". Fixed with a re-entry guard (test:
  `TickLoopTests.test_a_tick_during_start_cook_does_not_start_the_cook_twice`).

Every cook and pak prints a consolidated `[deploy-step]` timing line (wall time `w` +
per-thread CPU `c` per phase), so per-phase cost is visible in the Output Log:

```
[deploy-step] cook Chiv2Turbo: total=w9.35s cpu=0.40s {resolve_project=... ; collect_packages=... ; spawn=... ; cook_chunk_1_judge=... ; manifest=...}
[deploy-step] pak Chiv2Turbo: total=w3.11s cpu=0.20s {resolve_project=... ; ... ; run_create=... ; run_list=... ; publish=...}
```

If wall time is much larger than CPU for a phase, that phase was mostly waiting
(GIL/descheduling) rather than working. Async paking resolves project/engine on the
game thread before the worker starts, so Unreal API calls stay off the worker thread.

## Map markers: which maps get cooked

If the mod folder contains a **map marker** (`DA_MapMarker_V2` or a subclass; maintained with the
right-click / toolbar actions that ship with the SDK's `ModLoading/Python` scripts), the cook uses it:

- Every non-map asset in the folder is cooked as before.
- A map is cooked only if the marker needs it: its shipped maps, `ExtraRequiredMaps`, and the sublevel
  layers those need (`ShippedMapLayers`, recomputed from the registry at cook time). Other maps in the
  folder - backups, ignored maps - and their `<Map>_BuiltData` are skipped.
- Layers that live **outside** the mod folder (base game or another mod) are listed in the log but not
  cooked explicitly; they cannot be part of this mod's pak.
- The marker is read from the Asset Registry; no map is loaded. A marker that cannot be read fails the
  cook rather than silently cooking everything.
- The log says what happened: `[deploy] map marker X: cooking N map(s) ...; skipping M package(s)`.

Notes:

- **Editor only.** Outside the editor (`python deploy.py cook`) there is no `unreal` module to read the
  marker with, so every map is cooked. An SDK without the map marker scripts also cooks everything.
  `DEPLOY_IGNORE_MAP_MARKER=1` turns the marker off for a run.
- **Stale output is cleaned up.** The cooker keeps its output folder between runs and the pak packs
  whatever is in it. When the cook list shrinks (a map was dropped from the marker, an asset deleted), or
  a marker is used and the previous cook is unknown, that one cook runs without `-iterate`, which makes
  the engine clear the folder first (only the first chunk of a chunked cook does this). The manifest
  records `cooked_packages`, `skipped_packages` and `map_markers`.

## Mod markers (`DA_ModMarker_V2`): nested mods, bundles, paks

When the mod folder (or folders below it) contain V2 mod markers, the cook and pak use their plan (editor only; the
SDK's `modmarker` module reads the markers from the Asset Registry, nothing is loaded):

- **Version stamping.** Before cooking, `ModVersion` is stamped from the nearest `mod.json` `version` on the markers
  whose `mod.json` applies (a root marker walks up; a nested one only uses its own folder's), only when it changed.
  The same preflight stamps `ModVersion` on every mod actor class a marker lists (`ModActorClasses`) with that marker's
  effective version (only when it differs; the actor Blueprint is loaded, changed and saved). With the marker option
  `ApplyVersionToBundled` on the top marker, its version is also forced onto every other marker packed into its pak
  (merged nested ones and `BundledMarkers`) and their actors, overriding their own `mod.json`; `OwnPak` markers keep theirs.
  The log line is `[deploy] versions stamped: N marker(s), M mod actor class(es)`.
- **Paks.** One pak per root marker, named after its `ModId`; nested markers merge into their root's pak. A nested
  marker with `OwnPak` gets its own `<ModId>.pak` and is left out of the parent's. Size splitting (`--max-pak-size`)
  applies to each pak on its own (`<stem>_partN`). Only the first pak carries `ModAssetRegistry.bin`.
- **`_Content` folders.** Everything below ANY `_Content` folder inside a pak's tree (a marker's own, or one in a plain
  subfolder such as `Mods/ModLoading/_Content`) is mounted at the game root (content replacement), with the path below that
  `_Content` folder. If a real-path file and a `_Content` file target the same
  path, the `_Content` one wins.
- **`BundledMarkers`.** A marker may bundle markers from any directory: their folders are cooked with the mod and
  packed under their own real paths (their map markers and `ExcludedPaths` apply). Edits there invalidate the
  cook's provenance like the mod's own files do.
- **`ExcludedPaths`.** Patterns relative to the marker's folder, case-insensitive, matched on the asset path without
  extension: no wildcard = path prefix (`Old/`); `*` (any characters incl. `/`) and `?` (one character) must match the whole
  path or a parent folder (`*_bak`, `*/Backup`, `Maps/*_old`). Excluded packages are not cooked, and stale cooked
  files of them are not packed.
- **What it records.** `CookModManifest.json` gains `pak_plan`, `bundled_folders`, `cooked_packages`,
  `skipped_packages`; the pak step reads the plan from there, so a pak-only run packs what the cook planned.
- **Off switch / fallbacks.** `DEPLOY_IGNORE_MOD_MARKER=1` ignores the markers for a run. Outside the editor, or in an
  SDK without `modmarker`, nothing changes: one pak, everything in the folder. A marker tree that cannot be read
  fails the cook instead of guessing.
- **Mod registry note.** A registry release holds exactly one `.pak`, so a plan with several paks means each ships
  separately.

## Testing the multi-pak job and nested paks

- Unit: `python3 -m unittest discover -s tests` (164 pass; `PakAsyncTests` is an older thread test that flakes about 1 in 25).
  `tests/test_pak_groups.py` covers the multi-pak plan (nested merge, `OwnPak`, `_Content` mounts and collisions, per-pak size
  splitting), `tests/test_markers.py` the marker preflight and excludes, `tests/test_cook_maps.py` the map plan.
- Live, in the editor, on a collection with nested markers (one has `OwnPak`):
  ```python
  import deploy
  deploy.start_many(['/Game/Mods/AgMods/MyCollection'], dest_dir='P:/.../Saved/MyPaks', do_cook=True, do_pak=True)
  deploy.poll()   # until state is 'done' (or 'error' with the reason)
  ```
  Then read the `<stem>.txt` listing next to each pak: the root pak holds the nested folders, the `OwnPak` marker has its own
  `<ModId>.pak`, `_Content` files sit at the game root, and only the first pak has `ModAssetRegistry.bin`. Compare with
  `modmarker.plan(folder)['paks']` and the manifest's `pak_plan`. The pak stem is the marker's `ModId` (`mod.json` `name`
  when pak-safe, else the folder name), so a mod whose `mod.json` name differs from its folder is renamed by V2.

## CLI (non-editor)

```bash
python deploy.py cook MyMod [MyOther...] [--dest DIR] [--max-pak-size 200MB] [--dry-run]
python deploy.py pak  MyMod [--dest DIR]
```

<details>
<summary><b>Why this exists & the update model</b></summary>

The old setup split this tooling across every SDK's `Content/Python` and
`Content/Scripts/ModHandling`, which:
- duplicated the cook script per SDK (single `cook_mod.py`, drifting copies),
- worked only for one machine's hardcoded paths,
- made the widgets (binary `.uasset`, **unmergeable**) a `git pull` target, so
  anyone with local widget edits could not cleanly update.

That last point is the core design rule here:

> **Code/scripts are version-controlled text (diff/merge fine).**
> **Widgets (`.uasset`) are *replaceable artifacts*, updated by OVERWRITE, never merged.**

Update is therefore always *replace-not-merge*, and any user customization lives
in `Content/Scripts/_Local/` which updates **never touch**.

</details>

<details>
<summary><b>Project layout</b></summary>

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
    ModHandling/          # widget home: Mod Packer widgets (.uasset) + icons
    _Local/               # user customizations (git-ignored, never overwritten)
```
Git-ignored per-user `deploy.local.json` (next to the plugin root or CWD) +
`DEPLOY_*` env hold path/size overrides; never committed.

</details>

<details>
<summary><b>Portability & how it resolves paths</b></summary>

No hardcoded paths. The project is found from the script's own location; the
engine is resolved from the `.uproject`'s `EngineAssociation` the same way
Unreal does (registry / `%ProgramData%` / `%APPDATA%\\UnrealEngine\\Install.ini`),
with a per-user git-ignored `deploy.local.json` + `DEPLOY_*` env fallback.

See the epic `sdk_tweaks-vje` for the work breakdown.

</details>
