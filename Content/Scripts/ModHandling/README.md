# ModHandling (widgets)

The stock editor widget `.uasset` files live here and are **replaceable
artifacts**: they are updated by overwrite (`deploy.py sync-tooling`), never by
git merge. If you customize a widget, put your copy in `../_Local/` instead —
stock updates will not touch it, and the menu wiring prefers a `_Local/` copy
when one exists.

Files (upstream):
- `ModCookingInterface.uasset`
- `ModMarkerEntryWidget.uasset`
- `ModPackerSaveGame.uasset`
- `ModPackerWidget.uasset`
- `NewModWidget.uasset`

(`ModPackerWidget` / `ModMarkerEntryWidget` / `NewModWidget` are the ones wired
into the SDK's Content Browser context menus via `Content/Python/init_unreal.py`
— re-pointed at this plugin's paths once the integration ticket, `sdk_tweaks-vje.9`,
lands.)
