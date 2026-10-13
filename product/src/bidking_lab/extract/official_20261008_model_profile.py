"""Exact 76b7ca lookup and qualified model inputs, separate from frozen b049.

This profile reuses the established RankMap/empirical algorithms. Static Item
routes qualify support; they do not claim an official item-level drop graph.
6001 and 2511/4511 retain separate modeling responsibilities.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from types import MappingProxyType
from typing import Any, Mapping

from bidking_lab.extract import current_rankmap_package_contract as legacy
from bidking_lab.extract.bid_map_table import parse_bid_map_table
from bidking_lab.extract.item_table import parse_item_table
from bidking_lab.data import item_category_authority as category

GENERATION = "76b7ca-2026-10-08-723"
STEAM_BUILD_ID = "25795126"
AUTHORITY_SCHEMA = "bidking_official_20261008_model_authority_v1"
PACKAGE_BASELINE_ID = "package-data-2026-10-08-76b7ca-0.3.6"
CURRENT_RANKMAP_MAP_IDS = legacy.CURRENT_RANKMAP_MAP_IDS  # The same explicit 18-map output scope.
ADDED_HALLOWEEN_IDS = (1016015, 1046014, 1046015, 1046016, 1046017, 1096016)
AUTUMN_IDS = (1046013, 1056016, 1096012, 1096013, 1096014, 1096015)
EXPIRED_SEASON_IDS = (1916001,1916002,1916003,1916004,1916005,1916006,1916007,1916009,1916010,1916012,1916014,1916016,1916017,1916018,1916019,1916023,1916025,1916026,1916027,1916028,1916029,1916030,1916031,1916032,1916036,1916037,1916038)
EXCLUDED_MODEL_IDS = tuple(sorted((*AUTUMN_IDS, *EXPIRED_SEASON_IDS)))
UNION_IDS_SHA256 = "5c196e2b885e140c1c76b3a2fddca86f69c10533e89a0c26b808527f0c4f06e6"
ORDINARY_IDS_SHA256 = "b2f17ed59d2aa2e1b1000414ad6444467297f13202a70fb4df983002746b8f3e"
S2_IDS_SHA256 = "48d7ea83fd6ce28fc86130797ff0175d2a3410677b9313f90b57fd81c850d4c6"
CATALOG_CANONICAL_SHA256 = "68ccbfb4752b30d1f7b701b670bc7bd1e7bcd70954f26e2f3972cc276f36d55b"
DROPPABLE_CANONICAL_SHA256 = "5ef2e05c97db607fba08c35af100d23c5d09817d650aa4f0d3baeb1bb01438ca"
CATEGORY_IDENTITY = "36600b5116701a8152af992e51e1c7d6a0492fed0e841d36c79e0de285f89dde"

TEXT_ASSET_IDENTITIES = {
    "Item": {"bytes":561370,"sha256":"125550adca27edc14378886d7ab04c7b0b537015a1c59906e374c0659c1d4372","rows":1506,"columns":45},
    "BidMap": {"bytes":103972,"sha256":"67918be0098eb8f65772c34e0ef14bded7929878a832e64e448d2d9b724ba211","rows":246,"columns":28},
    "RankMap": {"bytes":88931,"sha256":"30cafbffea27bc8ed029b74bae12caba50bd8e3ead4986e2ea46546d81aee13a","rows":176,"columns":7},
    "Map": {"bytes":2586,"sha256":"3d0a74f55f5d91e99116f15b1b96270e10a3e3a91db05318a8e6c8ae0cb56b1d","rows":12,"columns":27},
}
BASELINE_IDENTITIES = {
    "data/processed/items.json":{"bytes":696961,"sha256":"1c41d6bd0900510c75fe7b3fd8b68e646d75219af2d3e73f3c8aecd84cace0f1"},
    "data/processed/items_droppable.json":{"bytes":357508,"sha256":"3d0eb1a17de6f433b7bebc01eb753f81b983060bfaa7ba0a10c0f88662926c98"},
    "data/processed/red_longtail_normalized_0627.json":{"bytes":1258,"sha256":"02701ac82528b0b398d7e9a39880a95449120de5b7d2bab9c7f5964c9143322a"},
    "data/processed/drop_weights_all_maps_0627.json":{"bytes":2664405,"sha256":"ce0db9680b53e393bc7a0db9e89e668ff3674701bdd524ed3b000f822bc8df11"},
    "data/processed/drop_table_priors_0627.json":{"bytes":21239,"sha256":"b9461153ccd18f82833b238f92f8599496ddc03977e889ef514b5932c870fe15"},
    "data/processed/rankmap_drop_weights_current.json":{"bytes":467354,"sha256":"6adc5792bae22bba8ca972b1397e30f7d651145b5b306516e18725d3809beec2"},
    "data/processed/rankmap_drop_priors_current.json":{"bytes":4966,"sha256":"d543ce6e998c6bc8dffa4d27b7453ede04f713cbe75be288ab7dc10032a46c26"},
    "data/processed/item_category_mem_0625.json":{"bytes":10947,"sha256":"8aaf29d89604e5a4cca605b29ef06a6564080a17aab98a65907fde1e84671d5d"},
}
OUTPUT_IDENTITIES = {
    "data/raw/tables/Item.txt":{"bytes":746484,"sha256":"380f1072fab85e7be8f2b03bc530a11b2b14681bad0366f411151294636fed40"},
    "data/raw/tables/BidMap.txt":{"bytes":138300,"sha256":"5b97e9ab4a8a3a583c239d6113b561200586e997cb41be79b8b2e0c447b9d020"},
    "data/processed/items.json":{"bytes":737910,"sha256":"7be0cb441b7b63e87e08d3f15ede1d3f4e2435e299715b13ff02fcc629287bab"},
    "data/processed/maps.json":{"bytes":181859,"sha256":"af272ffcb855633ecc78314ead660f08037f57c50e0197006e2cea3693831532"},
    "data/processed/items_droppable.json":{"bytes":322888,"sha256":"68e944020749968d301dca61ff2b12fccd21b32d93c22b72d394285cd9658f91"},
    "data/processed/red_longtail_normalized_0627.json":{"bytes":1326,"sha256":"4dead13f44eb1885cd3d1a82c8391ca39393bec222879f451c379620ac703300"},
    "data/processed/drop_weights_all_maps_0627.json":{"bytes":2556143,"sha256":"b7c55c9ee0a8b9211b4a70f53f1c67d4e3f037d4a2faa8e7550e1f240d4499d3"},
    "data/processed/drop_table_priors_0627.json":{"bytes":21239,"sha256":"83f2d76cfffb907fd3cd5116f9dcde880112255b818ea1e9dafe361eab901ef9"},
    "data/processed/rankmap_drop_weights_current.json":{"bytes":449613,"sha256":"08ba45ee553bdd08effe338b91237efcaef1115c82ab649eaf95a2ab72aa5453"},
    "data/processed/rankmap_drop_priors_current.json":{"bytes":4966,"sha256":"6db45943867d9028f0b2e2a13fc18f6ff10a8c4f0bc70e0cd8d68195ef139713"},
    "data/processed/rankmap_distribution_authority_current.json":{"bytes":24690,"sha256":"55b56d8c506685a806f0a9891e8999ac2d1e467ac25598c551d35579eaec4bfe"},
    "data/processed/item_category_authority_0_3_2.json":{"bytes":645491,"sha256":"48a76a52abf6aef7c3596ce216147e8dcae770a905e4121340f22851f6d2c6d8"},
}


class OfficialModelInputError(ValueError):
    """Missing, changed, mixed, or unsupported input; never a fallback success."""


def identity(raw: bytes) -> dict[str, Any]:
    return {"bytes":len(raw),"sha256":hashlib.sha256(raw).hexdigest()}


def _exact_raw(raw: Any, expected: Mapping[str, Any], label: str) -> bytes:
    if type(raw) is not bytes or identity(raw) != {k:expected[k] for k in ("bytes","sha256")}:
        raise OfficialModelInputError(f"exact input identity mismatch: {label}")
    return raw


@dataclass(frozen=True)
class OfficialModelProfile:
    generation: str
    steam_build_id: str
    authority_schema: str
    ordinary_rows: int
    s2_rows: int
    union_rows: int
    output_identities: Mapping[str, Mapping[str, Any]]


PROFILE = OfficialModelProfile(GENERATION,STEAM_BUILD_ID,AUTHORITY_SCHEMA,675,684,690,
                              MappingProxyType({p:MappingProxyType(v) for p,v in OUTPUT_IDENTITIES.items()}))


def select_model_profile(generation: Any) -> OfficialModelProfile | None:
    """Select only exact 76b7ca; other generations retain their own contracts."""
    return PROFILE if type(generation) is str and generation == GENERATION else None


def validate_profile_outputs(outputs: Mapping[str, bytes], *, generation: str) -> None:
    if select_model_profile(generation) is None or set(outputs) != set(OUTPUT_IDENTITIES):
        raise OfficialModelInputError("output profile/generation or member set mismatch")
    for path,pin in OUTPUT_IDENTITIES.items():
        _exact_raw(outputs[path],pin,path)


def _compact(value: Any) -> bytes:
    return json.dumps(value,ensure_ascii=False,separators=(",",":")).encode("utf-8")


def _records(value: Any) -> bytes:
    return json.dumps(value,ensure_ascii=False,indent=2,sort_keys=False).encode("utf-8")


def _text_rows(name: str, raw: bytes) -> list[list[str]]:
    expected=TEXT_ASSET_IDENTITIES[name]
    _exact_raw(raw,expected,name)
    rows=[line.split("\t") for line in raw.decode("utf-8-sig").splitlines()]
    if len(rows)!=expected["rows"] or {len(row) for row in rows}!={expected["columns"]}:
        raise OfficialModelInputError(f"row/column identity mismatch: {name}")
    return rows


def _category_document(parsed: Mapping[int,Any], catalog: list[dict[str,Any]], droppable: list[dict[str,Any]], legacy_categories: dict[str,int]) -> dict[str,Any]:
    """Use the same category record contract as the independently callable CLI."""
    ids={r["item_id"] for r in droppable}
    records=[]
    for i,item in sorted(parsed.items()):
        tags=[x for x in item.tags if x in category.TAXONOMY_IDS]
        records.append({"quality":item.quality,"value":item.value,"shape_w":item.shape_w,"shape_h":item.shape_h,
            "name_key":item.name_key,"desc_key":item.desc_key,"item_id":i,"taxonomy_tags":list(item.tags),
            "category_ids":tags,"primary_category":tags[-1] if tags else None,"catalog_present":True,
            "legacy_category_present":str(i) in legacy_categories,"current_random_drop_eligible":i in ids})
    sources={
        "item_table":{"archive_sha256":TEXT_ASSET_IDENTITIES["Item"]["sha256"],"columns":45,"compression":"none","hash_mode":"raw_uncompressed_bytes","rows":1506,"sha256":TEXT_ASSET_IDENTITIES["Item"]["sha256"]},
        "items_json":{"hash_mode":"canonical_json_utf8","overlap_rows":1506,"rows":1506,"sha256":category.sha256_json(catalog)},
        "legacy_category":{"hash_mode":"canonical_json_utf8","overlap_rows":len(legacy_categories),"rows":len(legacy_categories),"sha256":category.sha256_json(legacy_categories)},
        "items_droppable":{"hash_mode":"canonical_json_utf8","overlap_rows":690,"rows":690,"sha256":category.sha256_json(droppable)},
    }
    doc={"schema_version":category.SCHEMA_VERSION,"generation":"item-table-sha256:"+TEXT_ASSET_IDENTITIES["Item"]["sha256"],
         "taxonomy":category.TAXONOMY,"primary_category_policy":category.PRIMARY_CATEGORY_POLICY,"sources":sources,"records":records,
         "records_sha256":category.sha256_json(records),"authority_identity":""}
    doc["authority_identity"]=category.sha256_json(category.authority_identity_payload(doc))
    category.validate_authority_document(doc,expected_item_table_sha256=TEXT_ASSET_IDENTITIES["Item"]["sha256"])
    if doc["authority_identity"]!=CATEGORY_IDENTITY:
        raise OfficialModelInputError("new category authority identity mismatch")
    return doc


def build_inputs(*, text_assets: Mapping[str,bytes], baseline_inputs: Mapping[str,bytes]) -> tuple[dict[str,bytes],dict[str,Any]]:
    """Build all twelve exact lookup/model leaves; validate inputs before work."""
    if not set(TEXT_ASSET_IDENTITIES).issubset(text_assets) or set(baseline_inputs)!=set(BASELINE_IDENTITIES):
        raise OfficialModelInputError("missing or unexpected required input member")
    # Preflight every required leaf before producing any candidate.
    for name,pin in TEXT_ASSET_IDENTITIES.items(): _exact_raw(text_assets[name],pin,name)
    for path,pin in BASELINE_IDENTITIES.items(): _exact_raw(baseline_inputs[path],pin,path)
    rows={name:_text_rows(name,text_assets[name]) for name in TEXT_ASSET_IDENTITIES}
    baseline={path.rsplit("/",1)[-1]:legacy._strict_json(raw,label=path) for path,raw in baseline_inputs.items()}
    parsed=parse_item_table(rows["Item"])
    names={r["item_id"]:r["name"] for r in baseline["items.json"]}
    if len(names)!=1432 or not set(names).issubset(parsed):
        raise OfficialModelInputError("approved display catalog is not an exact subset")
    catalog=[]
    for i,item in sorted(parsed.items()):
        row=item.model_dump(exclude={"raw_row"})
        if i in names: row["name"]=names[i]
        catalog.append(row)
    if category.sha256_json(catalog)!=CATALOG_CANONICAL_SHA256:
        raise OfficialModelInputError("new catalog canonical identity mismatch")
    catalog_by={r["item_id"]:r for r in catalog}
    old_ids={r["item_id"] for r in baseline["items_droppable.json"]}
    if len(old_ids)!=717 or legacy._ids_sha256(tuple(sorted(old_ids)))!=legacy.CURRENT_MODEL_UNION_ITEM_IDS_SHA256:
        raise OfficialModelInputError("old qualified support identity mismatch")
    hall_only={i for i,item in parsed.items() if item.raw_row[39]=="[601]"}
    removed=old_ids&hall_only
    if tuple(sorted(removed))!=EXCLUDED_MODEL_IDS:
        raise OfficialModelInputError("new exclusive-hall lifecycle mismatch")
    union=tuple(sorted(old_ids-removed|set(ADDED_HALLOWEEN_IDS)))
    if len(union)!=690 or legacy._ids_sha256(union)!=UNION_IDS_SHA256:
        raise OfficialModelInputError("new qualified support identity mismatch")
    if any(501 not in json.loads(parsed[i].raw_row[39]) or parsed[i].quality!=6 for i in ADDED_HALLOWEEN_IDS):
        raise OfficialModelInputError("new Halloween ordinary route mismatch")
    droppable=[{**catalog_by[i],"_droppable_src":"catalog_mem"} for i in union]
    if category.sha256_json(droppable)!=DROPPABLE_CANONICAL_SHA256:
        raise OfficialModelInputError("new droppable canonical identity mismatch")
    items=legacy._current_item_semantics(rows["Item"])
    fitted=legacy._current_item_fitted_values(items,union)
    rank={int(r[0]):r for r in rows["RankMap"]}
    outputs={f"data/raw/tables/{name}.txt":legacy.build_official_20261008_package_table(name,text_assets[name])[0] for name in ("Item","BidMap")}
    outputs["data/processed/items.json"]=_records(catalog)
    outputs["data/processed/maps.json"]=_records([v.model_dump(exclude={"raw_row"}) for _,v in sorted(parse_bid_map_table(rows["BidMap"]).items())])
    outputs["data/processed/items_droppable.json"]=_compact(droppable)
    outputs["data/processed/red_longtail_normalized_0627.json"]=_compact({str(i):fitted[i] for i in sorted(items) if items[i]["quality"]==6 and math.prod(items[i]["shape"])>0 and fitted[i]!=items[i]["value"]})
    scopes={};weight_results={};prior_results={}
    for label,wn,pn in (("ordinary","drop_weights_all_maps_0627.json","drop_table_priors_0627.json"),("ranked","rankmap_drop_weights_current.json","rankmap_drop_priors_current.json")):
        targets={};priors={};map_supports={}
        for mid,entry in baseline[wn].items():
            map_id=int(mid);s2=5601<=map_id<=5611
            support=tuple(sorted({int(i) for i in entry["items"]}-removed|(set() if s2 else set(ADDED_HALLOWEEN_IDS))))
            if len(support)!=(684 if s2 else 675) or legacy._ids_sha256(support)!=(S2_IDS_SHA256 if s2 else ORDINARY_IDS_SHA256):
                raise OfficialModelInputError(f"map support identity mismatch: {mid}")
            previous_prior=baseline[pn].get(mid)
            masses=previous_prior["quality_probs"] if previous_prior else legacy._entry_quality_probabilities(entry,items)
            probabilities=legacy._probabilities_for_support(support=support,items=items,anchors=json.loads(rank[map_id][6]),quality_probs=masses)
            targets[mid]={"count":entry["count"],"n_items":len(support),"items":{str(i):[probabilities[i],items[i]["value"],fitted[i]] for i in support}}
            if previous_prior: priors[mid]=legacy._updated_quality_prior(previous_entry=entry,current_entry=targets[mid],previous_prior=previous_prior,items=items)
            map_supports[mid]={"rows":len(support),"ids_sha256":legacy._ids_sha256(support)}
        outputs[f"data/processed/{wn}"]=_compact(targets)
        outputs[f"data/processed/{pn}"]=_compact(priors)
        scopes[label]={"maps":len(targets),"priors":len(priors),"map_ids_sha256":legacy._ids_sha256(tuple(int(x) for x in targets)),"supports":map_supports}
        weight_results[label]=targets;prior_results[label]=priors
    if any(weight_results["ordinary"][str(i)]!=weight_results["ranked"][str(i)] or prior_results["ordinary"][str(i)]!=prior_results["ranked"][str(i)] for i in range(2101,2108)):
        raise OfficialModelInputError("overlapping ordinary/ranked scope drift")
    authority={
        "schema":AUTHORITY_SCHEMA,"package_generation":GENERATION,"steam_build_id":STEAM_BUILD_ID,
        "authority_mode":"rankmap_drop_model_algorithm_prior","official_distribution_authority":False,"model_distribution_authority":True,
        "claims_item_level_drop_graph":False,"drop_or_dropmap_absence_is_not_a_blocker":True,"runtime_open_claim":False,
        "algorithm":{"probability":"retained_quality_mass_times_rankmap_col6_value_weight_within_quality","fitted_value":"existing_shape_floor_and_per_cell_median_qualified_union690","quality_values":"q1_through_q5_previous_empirical_preserved;q6_previous_empirical_scaled_by_current_model_delta"},
        "support":{"union_rows":690,"union_ids_sha256":UNION_IDS_SHA256,"ordinary_rows":675,"ordinary_ids_sha256":ORDINARY_IDS_SHA256,"s2_rows":684,"s2_ids_sha256":S2_IDS_SHA256,"excluded_hall_only_ids":sorted(removed),"added_halloween_ids":sorted(ADDED_HALLOWEEN_IDS)},
        "sources":{"text_assets":{n:{"rows":len(rows[n]),"columns":len(rows[n][0]),**identity(text_assets[n])} for n in ("Item","BidMap","RankMap","Map")},"baseline":{p:identity(baseline_inputs[p]) for p in BASELINE_IDENTITIES}},
        "scopes":scopes,"outputs":{p:identity(raw) for p,raw in outputs.items()},
        "excluded_scopes":{"6001":"separate6010_model_not_built_in_SRC2A","2511_4511":"separate_empirical_calibration_preserved"},
        "s2_quality_calibration":legacy._s2_empirical_calibration_receipt(prior_results["ranked"]["5601"]["quality_probs"]),
    }
    outputs["data/processed/rankmap_distribution_authority_current.json"]=_records(authority)
    document=_category_document(parsed,catalog,droppable,baseline["item_category_mem_0625.json"])
    outputs["data/processed/item_category_authority_0_3_2.json"]=(json.dumps(document,ensure_ascii=False,sort_keys=True,indent=2)+"\n").encode("utf-8")
    validate_profile_outputs(outputs,generation=GENERATION)
    return outputs,{"schema":"bidking_official_20261008_inputs_receipt_v1","generation":GENERATION,"steam_build_id":STEAM_BUILD_ID,
                    "inputs":{"text_assets":TEXT_ASSET_IDENTITIES,"baseline":BASELINE_IDENTITIES},"outputs":{p:identity(v) for p,v in outputs.items()},
                    "support":{"union_rows":690,"ordinary_rows":675,"s2_rows":684,"union_ids_sha256":UNION_IDS_SHA256},
                    "category_identity":CATEGORY_IDENTITY,"lifecycle":"qualified_model_inputs_not_built_or_runtime_verified",
                    "not_run":["6001_distribution","2511_4511_successor_calibration","presolve_regeneration","native","build","production"]}


__all__=("GENERATION","STEAM_BUILD_ID","AUTHORITY_SCHEMA","PACKAGE_BASELINE_ID","CURRENT_RANKMAP_MAP_IDS","PROFILE","TEXT_ASSET_IDENTITIES","BASELINE_IDENTITIES","OUTPUT_IDENTITIES",
         "CATALOG_CANONICAL_SHA256","DROPPABLE_CANONICAL_SHA256","CATEGORY_IDENTITY","UNION_IDS_SHA256","ORDINARY_IDS_SHA256","S2_IDS_SHA256",
         "ADDED_HALLOWEEN_IDS","AUTUMN_IDS","EXPIRED_SEASON_IDS","EXCLUDED_MODEL_IDS","OfficialModelProfile","OfficialModelInputError",
         "select_model_profile","validate_profile_outputs","build_inputs")
