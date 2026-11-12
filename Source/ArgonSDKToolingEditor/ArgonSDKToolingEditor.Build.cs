// Copyright Chiv2-Community. Editor-only tooling module.
using UnrealBuildTool;

public class ArgonSDKToolingEditor : ModuleRules
{
	public ArgonSDKToolingEditor(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;

		// Kept intentionally minimal: the library only uses the reflection system
		// (FProperty / FScriptArrayHelper) plus UTexture2D. These APIs are identical
		// on UE 4.25 and 4.25-Plus, so no engine-version branching is required.
		PublicDependencyModuleNames.AddRange(new string[]
		{
			"Core",
			"CoreUObject",
			"Engine"
		});
	}
}
