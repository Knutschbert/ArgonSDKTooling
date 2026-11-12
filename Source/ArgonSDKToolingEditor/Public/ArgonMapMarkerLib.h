// Copyright Chiv2-Community. Editor-only tooling module.
#pragma once

#include "CoreMinimal.h"
#include "UObject/SoftObjectPath.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "ArgonMapMarkerLib.generated.h"

/**
 * Editor-only helpers for reading and writing the `Maps` array on a
 * DA_MapMarker_V2 DataAsset (an array of the Blueprint UserDefinedStruct
 * F_ShippedMap).
 *
 * UE 4.25 Python cannot construct a UserDefinedStruct, grow a struct array, or
 * call Blueprint-defined functions (`call_method` only exists from 4.26). These
 * C++ BlueprintCallable statics ARE exposed to Python as
 * `unreal.ArgonMapMarkerLib.<snake_case>(...)`, so mapmarker.py drives the marker
 * through here. The struct itself stays a Blueprint asset: this library never
 * names F_ShippedMap in C++ — it resolves the `Maps` array and the struct members
 * entirely through the reflection system, matching members by their authored
 * names (Map, State, LoadingScreen, MapName, MapDescription, GamemodeType,
 * AttackingFaction). That keeps it agnostic to the struct's mangled internal
 * field names and to future field additions, and identical on 4.25 / 4.25-Plus.
 *
 * `State` is a 2-value UserDefinedEnum (E_MapShipState: 0 = Shipped, 1 = Held),
 * surfaced here as the bool `bHeld` so Python never has to touch the enum type.
 * Loading-screen references cross the boundary as FSoftObjectPath (the struct
 * member is a TSoftObjectPtr<UTexture2D>, set/read by path — nothing is loaded).
 */
UCLASS()
class ARGONSDKTOOLINGEDITOR_API UArgonMapMarkerLib : public UBlueprintFunctionLibrary
{
	GENERATED_BODY()

public:
	/** Number of entries in the marker's `Maps` array, or -1 if it has no such array. */
	UFUNCTION(BlueprintCallable, Category = "Argon|MapMarker")
	static int32 GetShippedMapCount(UObject* Marker);

	/**
	 * Read one `Maps` row by index. Returns false if the marker/array/index is invalid.
	 * Missing members are returned as their zero value rather than failing.
	 */
	UFUNCTION(BlueprintCallable, Category = "Argon|MapMarker")
	static bool GetShippedMap(UObject* Marker, int32 Index,
		FSoftObjectPath& OutMap, bool& bOutHeld, FSoftObjectPath& OutLoadingScreen,
		FString& OutMapName, FString& OutDescription,
		int32& OutGamemodeType, int32& OutAttackingFaction);

	/**
	 * Add a `Maps` row for `Map`, or update the existing row with that Map, setting
	 * every field. Returns the row index, or -1 on failure. Marks the package dirty;
	 * the caller still saves (mapmarker.py force-saves).
	 */
	UFUNCTION(BlueprintCallable, Category = "Argon|MapMarker")
	static int32 AddOrUpdateShippedMap(UObject* Marker, const FSoftObjectPath& Map, bool bHeld,
		const FSoftObjectPath& LoadingScreen, const FString& MapName, const FString& Description,
		int32 GamemodeType, int32 AttackingFaction);

	/**
	 * Overwrite only the AR-derived summary fields (MapName/Description/GamemodeType/
	 * AttackingFaction) of the existing row for `Map`, leaving Map/State/LoadingScreen
	 * untouched. Returns false if no row has that Map. This is the AR-refresh entry point.
	 */
	UFUNCTION(BlueprintCallable, Category = "Argon|MapMarker")
	static bool SetShippedMapSummary(UObject* Marker, const FSoftObjectPath& Map,
		const FString& MapName, const FString& Description,
		int32 GamemodeType, int32 AttackingFaction);

	/** Remove the row whose Map equals `Map`. Returns true if one was removed. */
	UFUNCTION(BlueprintCallable, Category = "Argon|MapMarker")
	static bool RemoveShippedMap(UObject* Marker, const FSoftObjectPath& Map);
};
