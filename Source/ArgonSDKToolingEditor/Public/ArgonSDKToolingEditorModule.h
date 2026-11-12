// Copyright Chiv2-Community. Editor-only tooling module.
#pragma once

#include "CoreMinimal.h"
#include "Modules/ModuleInterface.h"

/**
 * Editor-only module for the Argon SDK Tooling plugin.
 *
 * It exists so the plugin can ship C++ BlueprintFunctionLibrary helpers that the
 * in-editor Python tooling (mapmarker.py) can call. Nothing here is loaded in a
 * packaged/runtime (-game) build: the module Type is "Editor".
 */
class FArgonSDKToolingEditorModule : public IModuleInterface
{
public:
	virtual void StartupModule() override;
	virtual void ShutdownModule() override;
};
