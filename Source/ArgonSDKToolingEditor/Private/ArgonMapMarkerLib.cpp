// Copyright Chiv2-Community. Editor-only tooling module.
#include "ArgonMapMarkerLib.h"
#include "UObject/UnrealType.h"
#include "UObject/Field.h"
#include "UObject/SoftObjectPtr.h"

// Authored (friendly) member names on the F_ShippedMap UserDefinedStruct.
namespace ArgonMapMarkerFields
{
	static const TCHAR* Map              = TEXT("Map");
	static const TCHAR* State            = TEXT("State");
	static const TCHAR* LoadingScreen    = TEXT("LoadingScreen");
	static const TCHAR* MapName          = TEXT("MapName");
	static const TCHAR* MapDescription   = TEXT("MapDescription");
	static const TCHAR* GamemodeType     = TEXT("GamemodeType");
	static const TCHAR* AttackingFaction = TEXT("AttackingFaction");
}

namespace
{
	/** Resolve the marker's `Maps` FArrayProperty and the element UScriptStruct. */
	bool ResolveMapsArray(UObject* Marker, FArrayProperty*& OutArrayProp, UScriptStruct*& OutStruct)
	{
		OutArrayProp = nullptr;
		OutStruct = nullptr;
		if (!Marker)
		{
			return false;
		}
		FArrayProperty* ArrayProp = CastField<FArrayProperty>(Marker->GetClass()->FindPropertyByName(TEXT("Maps")));
		if (!ArrayProp)
		{
			return false;
		}
		FStructProperty* ElemProp = CastField<FStructProperty>(ArrayProp->Inner);
		if (!ElemProp || !ElemProp->Struct)
		{
			return false;
		}
		OutArrayProp = ArrayProp;
		OutStruct = ElemProp->Struct;
		return true;
	}

	/**
	 * Find a struct member by its authored (friendly) name. UserDefinedStruct members
	 * have mangled FNames (e.g. "MapName_11_<GUID>"); GetAuthoredNameForField maps them
	 * back to the editor label. A prefix-strip fallback covers the (single-token) field
	 * names here in case the authored-name API ever returns the raw name.
	 */
	FProperty* FindMember(UScriptStruct* Struct, const TCHAR* Authored)
	{
		for (TFieldIterator<FProperty> It(Struct); It; ++It)
		{
			FProperty* P = *It;
			if (Struct->GetAuthoredNameForField(P).Equals(Authored, ESearchCase::IgnoreCase))
			{
				return P;
			}
		}
		for (TFieldIterator<FProperty> It(Struct); It; ++It)
		{
			FProperty* P = *It;
			FString Name = P->GetName();
			int32 Underscore = INDEX_NONE;
			if (Name.FindChar(TEXT('_'), Underscore))
			{
				Name = Name.Left(Underscore);
			}
			if (Name.Equals(Authored, ESearchCase::IgnoreCase))
			{
				return P;
			}
		}
		return nullptr;
	}

	// ---- typed member accessors (Elem = base address of the struct instance) ----

	void SetSoftPathMember(UScriptStruct* S, void* Elem, const TCHAR* Field, const FSoftObjectPath& Value)
	{
		if (FStructProperty* P = CastField<FStructProperty>(FindMember(S, Field)))
		{
			if (P->Struct == TBaseStructure<FSoftObjectPath>::Get())
			{
				*P->ContainerPtrToValuePtr<FSoftObjectPath>(Elem) = Value;
			}
		}
	}

	FSoftObjectPath GetSoftPathMember(UScriptStruct* S, const void* Elem, const TCHAR* Field)
	{
		if (FStructProperty* P = CastField<FStructProperty>(FindMember(S, Field)))
		{
			if (P->Struct == TBaseStructure<FSoftObjectPath>::Get())
			{
				return *P->ContainerPtrToValuePtr<FSoftObjectPath>(Elem);
			}
		}
		return FSoftObjectPath();
	}

	void SetSoftObjectMember(UScriptStruct* S, void* Elem, const TCHAR* Field, const FSoftObjectPath& Value)
	{
		if (FSoftObjectProperty* P = CastField<FSoftObjectProperty>(FindMember(S, Field)))
		{
			*P->ContainerPtrToValuePtr<FSoftObjectPtr>(Elem) = FSoftObjectPtr(Value);
		}
	}

	FSoftObjectPath GetSoftObjectMember(UScriptStruct* S, const void* Elem, const TCHAR* Field)
	{
		if (FSoftObjectProperty* P = CastField<FSoftObjectProperty>(FindMember(S, Field)))
		{
			return P->ContainerPtrToValuePtr<FSoftObjectPtr>(Elem)->ToSoftObjectPath();
		}
		return FSoftObjectPath();
	}

	void SetStringMember(UScriptStruct* S, void* Elem, const TCHAR* Field, const FString& Value)
	{
		if (FStrProperty* P = CastField<FStrProperty>(FindMember(S, Field)))
		{
			*P->ContainerPtrToValuePtr<FString>(Elem) = Value;
		}
	}

	FString GetStringMember(UScriptStruct* S, const void* Elem, const TCHAR* Field)
	{
		if (FStrProperty* P = CastField<FStrProperty>(FindMember(S, Field)))
		{
			return *P->ContainerPtrToValuePtr<FString>(Elem);
		}
		return FString();
	}

	void SetIntMember(UScriptStruct* S, void* Elem, const TCHAR* Field, int32 Value)
	{
		if (FIntProperty* P = CastField<FIntProperty>(FindMember(S, Field)))
		{
			*P->ContainerPtrToValuePtr<int32>(Elem) = Value;
		}
	}

	int32 GetIntMember(UScriptStruct* S, const void* Elem, const TCHAR* Field)
	{
		if (FIntProperty* P = CastField<FIntProperty>(FindMember(S, Field)))
		{
			return *P->ContainerPtrToValuePtr<int32>(Elem);
		}
		return 0;
	}

	// State is a byte-backed UserDefinedEnum (0 = Shipped, 1 = Held). Handle both the
	// FEnumProperty and plain FByteProperty representations.
	void SetEnumMemberAsInt(UScriptStruct* S, void* Elem, const TCHAR* Field, int64 Value)
	{
		FProperty* P = FindMember(S, Field);
		if (!P)
		{
			return;
		}
		if (FEnumProperty* EP = CastField<FEnumProperty>(P))
		{
			EP->GetUnderlyingProperty()->SetIntPropertyValue(P->ContainerPtrToValuePtr<void>(Elem), Value);
		}
		else if (FNumericProperty* NP = CastField<FNumericProperty>(P))
		{
			NP->SetIntPropertyValue(P->ContainerPtrToValuePtr<void>(Elem), Value);
		}
	}

	int64 GetEnumMemberAsInt(UScriptStruct* S, const void* Elem, const TCHAR* Field)
	{
		FProperty* P = FindMember(S, Field);
		if (!P)
		{
			return 0;
		}
		if (FEnumProperty* EP = CastField<FEnumProperty>(P))
		{
			return EP->GetUnderlyingProperty()->GetSignedIntPropertyValue(const_cast<void*>(P->ContainerPtrToValuePtr<void>(Elem)));
		}
		if (FNumericProperty* NP = CastField<FNumericProperty>(P))
		{
			return NP->GetSignedIntPropertyValue(const_cast<void*>(P->ContainerPtrToValuePtr<void>(Elem)));
		}
		return 0;
	}

	/** Index of the row whose Map member equals `Map`, or INDEX_NONE. */
	int32 FindRowIndex(UScriptStruct* S, FScriptArrayHelper& Helper, const FSoftObjectPath& Map)
	{
		for (int32 i = 0; i < Helper.Num(); ++i)
		{
			if (GetSoftPathMember(S, Helper.GetRawPtr(i), ArgonMapMarkerFields::Map) == Map)
			{
				return i;
			}
		}
		return INDEX_NONE;
	}
}

int32 UArgonMapMarkerLib::GetShippedMapCount(UObject* Marker)
{
	FArrayProperty* ArrayProp = nullptr;
	UScriptStruct* Struct = nullptr;
	if (!ResolveMapsArray(Marker, ArrayProp, Struct))
	{
		return -1;
	}
	FScriptArrayHelper Helper(ArrayProp, ArrayProp->ContainerPtrToValuePtr<void>(Marker));
	return Helper.Num();
}

bool UArgonMapMarkerLib::GetShippedMap(UObject* Marker, int32 Index,
	FSoftObjectPath& OutMap, bool& bOutHeld, FSoftObjectPath& OutLoadingScreen,
	FString& OutMapName, FString& OutDescription,
	int32& OutGamemodeType, int32& OutAttackingFaction)
{
	OutMap = FSoftObjectPath();
	bOutHeld = false;
	OutLoadingScreen = FSoftObjectPath();
	OutMapName.Reset();
	OutDescription.Reset();
	OutGamemodeType = 0;
	OutAttackingFaction = 0;

	FArrayProperty* ArrayProp = nullptr;
	UScriptStruct* Struct = nullptr;
	if (!ResolveMapsArray(Marker, ArrayProp, Struct))
	{
		return false;
	}
	FScriptArrayHelper Helper(ArrayProp, ArrayProp->ContainerPtrToValuePtr<void>(Marker));
	if (Index < 0 || Index >= Helper.Num())
	{
		return false;
	}
	const void* Elem = Helper.GetRawPtr(Index);
	OutMap = GetSoftPathMember(Struct, Elem, ArgonMapMarkerFields::Map);
	bOutHeld = GetEnumMemberAsInt(Struct, Elem, ArgonMapMarkerFields::State) != 0;
	OutLoadingScreen = GetSoftObjectMember(Struct, Elem, ArgonMapMarkerFields::LoadingScreen);
	OutMapName = GetStringMember(Struct, Elem, ArgonMapMarkerFields::MapName);
	OutDescription = GetStringMember(Struct, Elem, ArgonMapMarkerFields::MapDescription);
	OutGamemodeType = GetIntMember(Struct, Elem, ArgonMapMarkerFields::GamemodeType);
	OutAttackingFaction = GetIntMember(Struct, Elem, ArgonMapMarkerFields::AttackingFaction);
	return true;
}

int32 UArgonMapMarkerLib::AddOrUpdateShippedMap(UObject* Marker, const FSoftObjectPath& Map, bool bHeld,
	const FSoftObjectPath& LoadingScreen, const FString& MapName, const FString& Description,
	int32 GamemodeType, int32 AttackingFaction)
{
	FArrayProperty* ArrayProp = nullptr;
	UScriptStruct* Struct = nullptr;
	if (!ResolveMapsArray(Marker, ArrayProp, Struct) || !Map.IsValid())
	{
		return -1;
	}
	Marker->Modify();
	FScriptArrayHelper Helper(ArrayProp, ArrayProp->ContainerPtrToValuePtr<void>(Marker));
	int32 Index = FindRowIndex(Struct, Helper, Map);
	if (Index == INDEX_NONE)
	{
		Index = Helper.AddValue();
	}
	void* Elem = Helper.GetRawPtr(Index);
	SetSoftPathMember(Struct, Elem, ArgonMapMarkerFields::Map, Map);
	SetEnumMemberAsInt(Struct, Elem, ArgonMapMarkerFields::State, bHeld ? 1 : 0);
	SetSoftObjectMember(Struct, Elem, ArgonMapMarkerFields::LoadingScreen, LoadingScreen);
	SetStringMember(Struct, Elem, ArgonMapMarkerFields::MapName, MapName);
	SetStringMember(Struct, Elem, ArgonMapMarkerFields::MapDescription, Description);
	SetIntMember(Struct, Elem, ArgonMapMarkerFields::GamemodeType, GamemodeType);
	SetIntMember(Struct, Elem, ArgonMapMarkerFields::AttackingFaction, AttackingFaction);
	Marker->MarkPackageDirty();
	return Index;
}

bool UArgonMapMarkerLib::SetShippedMapSummary(UObject* Marker, const FSoftObjectPath& Map,
	const FString& MapName, const FString& Description,
	int32 GamemodeType, int32 AttackingFaction)
{
	FArrayProperty* ArrayProp = nullptr;
	UScriptStruct* Struct = nullptr;
	if (!ResolveMapsArray(Marker, ArrayProp, Struct))
	{
		return false;
	}
	FScriptArrayHelper Helper(ArrayProp, ArrayProp->ContainerPtrToValuePtr<void>(Marker));
	const int32 Index = FindRowIndex(Struct, Helper, Map);
	if (Index == INDEX_NONE)
	{
		return false;
	}
	Marker->Modify();
	void* Elem = Helper.GetRawPtr(Index);
	SetStringMember(Struct, Elem, ArgonMapMarkerFields::MapName, MapName);
	SetStringMember(Struct, Elem, ArgonMapMarkerFields::MapDescription, Description);
	SetIntMember(Struct, Elem, ArgonMapMarkerFields::GamemodeType, GamemodeType);
	SetIntMember(Struct, Elem, ArgonMapMarkerFields::AttackingFaction, AttackingFaction);
	Marker->MarkPackageDirty();
	return true;
}

bool UArgonMapMarkerLib::RemoveShippedMap(UObject* Marker, const FSoftObjectPath& Map)
{
	FArrayProperty* ArrayProp = nullptr;
	UScriptStruct* Struct = nullptr;
	if (!ResolveMapsArray(Marker, ArrayProp, Struct))
	{
		return false;
	}
	FScriptArrayHelper Helper(ArrayProp, ArrayProp->ContainerPtrToValuePtr<void>(Marker));
	const int32 Index = FindRowIndex(Struct, Helper, Map);
	if (Index == INDEX_NONE)
	{
		return false;
	}
	Marker->Modify();
	Helper.RemoveValues(Index, 1);
	Marker->MarkPackageDirty();
	return true;
}
