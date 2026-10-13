"""Current 6001 slotted algorithm model, not an official item-level drop graph.

The two retired sets each occupy at least one physical slot. Public objects
replace slots, never a second warehouse. Ordinary quality mass is explicitly
borrowed from current 2601; within-tier weights use RankMap6001's value curve.
RankMap col3 is NOT a warehouse count range. No settlement labels are read.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
from types import MappingProxyType

import numpy as np

from bidking_lab.extract.item_table import parse_item_table
from bidking_lab.extract.tables import decode_table_text, iter_table_rows
from bidking_lab.extract.current_rankmap_package_contract import (
    _col6_weight, _probabilities_for_support,
)

GENERATION = '76b7ca-2026-10-08-723'
MODEL_VERSION = 'auction_hall_6001_slotted_algorithm_036_v1'
QUALITY_KEYS = ('q1', 'q3', 'q4', 'q5', 'q6')
EVENT_IDS = (1016007,1016008,1016009,1016010,1016011,1016012,1016013,
             1016014,1036006,1036007,1036008,1046013,1056016,1096011,
             1096012,1096013,1096014,1096015)
SEASON_IDS = (1916001,1916002,1916003,1916004,1916005,1916006,1916007,
              1916009,1916010,1916012,1916014,1916016,1916017,1916018,
              1916019,1916023,1916025,1916026,1916027,1916028,1916029,
              1916030,1916031,1916032,1916036,1916037,1916038)
INPUTS = {
    'data/raw/tables/Item.txt': (746484, '380f1072fab85e7be8f2b03bc530a11b2b14681bad0366f411151294636fed40'),
    'data/raw/tables/BidMap.txt': (138300, '5b97e9ab4a8a3a583c239d6113b561200586e997cb41be79b8b2e0c447b9d020'),
    'data/raw/tables/RankMap.txt': (118344, 'e6b4fae0caa8e71b342c79c3fc2235a9285ac4d28b918604af088b156885d1a7'),
    'data/processed/drop_weights_all_maps_0627.json': (2556143, 'b7c55c9ee0a8b9211b4a70f53f1c67d4e3f037d4a2faa8e7550e1f240d4499d3'),
    'data/processed/drop_table_priors_0627.json': (21239, '83f2d76cfffb907fd3cd5116f9dcde880112255b818ea1e9dafe361eab901ef9'),
}
SOURCE_CLASSIFICATION = {
    'qualification': 'postupdate_unknown_warehouse_source',
    'reason': 'official_20260903_map_unopened', 'map_id': 6001,
    'table_status': 'verified', 'explicit_source': '',
    'source_generation': GENERATION, 'map_profile_generation': GENERATION,
    'game_version': GENERATION, 'map_table_build': '25795126',
    'map_table_sha256': '7abe97f33ec48c8b18887de5abcddc1969b5decc2b9be30b1f8fd73f5b71c9fc',
    'expected_map_table_sha256': '7abe97f33ec48c8b18887de5abcddc1969b5decc2b9be30b1f8fd73f5b71c9fc',
    'map_source_contract_sha256': 'b6e0635f8033bb20dbac7a6c6a985fe5bd4d923da1bf9a2d3d21dd524273a6f3',
    'expected_map_source_contract_sha256': 'b6e0635f8033bb20dbac7a6c6a985fe5bd4d923da1bf9a2d3d21dd524273a6f3',
    'auction_mode': 'auction_hall', 'owner_evidence_status': 'absent',
    'source_conflict': True, 'quarantine_required': True,
    'official_distribution_eligible': False, 'config': None,
}
# A caller may supply a separately frozen descriptive prior. Missing is not
# filled from RankMap col3 or BidMap's one pool invocation.
COUNT_PRIOR = MappingProxyType({
    'center': 122.14285714285714, 'sigma': 24.42857142857143,
    'source': 'fixed-cloud train7 descriptive count, c1d23d-2026-09-24-591/map6001',
    'parameter_generation': 'c1d23d-2026-09-24-591', 'train_sessions': 7,
    'source_qualification': 'unknown_or_conflicting',
    'input_sha256': '3a600503cf88814f3204304421f190afe83f18001cee0b6924f9348eb4585d71',
    'split_salt': 'bidking-036-session-split-v1:',
    'sigma_source': 'model assumption: 20% of train-only mean; not fitted to holdout',
    'current_training_claim': False, 'official_distribution_authority': False,
})


class ModelInputError(ValueError):
    """Input/scope failure, not evidence of an impossible real warehouse."""


class MathematicalConflict(ModelInputError):
    """A contradiction proved from hard arithmetic/public facts."""


def _uint(value, field, maximum=512, minimum=0):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ModelInputError('invalid_' + field)
    return value


def scope_state(snapshot):
    if type(snapshot) is not dict or type(snapshot.get('ui_contract', {})) is not dict:
        return 'invalid_owner'
    owners = [snapshot]
    for key in ('context', 'source'):
        owner = snapshot.get('ui_contract', {}).get(key, {})
        if type(owner) is not dict:
            return 'invalid_owner'
        owners.append(owner)
    maps, sessions, phases, sources, stable = [], [], [], [], []
    for owner in owners:
        for key, values in (('map_id',maps), ('session_id',sessions), ('phase',phases),
                            ('warehouse_source',sources), ('warehouse_game_version_session_stable',stable)):
            if key in owner:
                values.append(owner[key])
        for key in ('model_map_id', 'physical_catalog_map_id'):
            if owner.get(key) is not None and (type(owner[key]) is not int or owner[key] != 6001):
                return 'map_alias_conflict'
        if owner.get('map_alias') or owner.get('map_alias_mode'):
            return 'map_alias_conflict'
        if owner.get('formal_mode_reason') == 'game_generation_unverified':
            return 'generation_conflict'
        if 'warehouse_source_generation' in owner and (type(owner['warehouse_source_generation']) is not str
                or owner['warehouse_source_generation'] != GENERATION):
            return 'generation_conflict'
        for key,expected in (('warehouse_source_qualification',SOURCE_CLASSIFICATION['qualification']),
            ('warehouse_owner_evidence_status','absent'),('official_distribution_eligible',False)):
            if key in owner and (type(owner[key]) is not type(expected) or owner[key]!=expected):
                return 'source_identity_or_owner_conflict'
    if not maps or any(type(v) not in (int,str) or v not in (6001,'6001') for v in maps):
        return 'map_conflict'
    if not sessions or any(type(v) is not str or v != sessions[0] for v in sessions):
        return 'session_conflict'
    session = sessions[0]
    tail = session[5:]
    if not session.startswith('6001:') or len(tail) > 20 or not re.fullmatch('[1-9][0-9]*',tail) or int(tail) > 2**64-1:
        return 'session_conflict'
    for owner in owners:
        for key in ('epoch','session_epoch'):
            if key in owner:
                v = owner[key]
                if not ((type(v) is str and v == tail) or (type(v) is int and v == int(tail))):
                    return 'epoch_conflict'
    if not phases or any(type(v) is not str or v not in ('reading','bidding') or v != phases[0] for v in phases):
        return 'not_live_pre_settlement'
    if not stable or any(v is not True for v in stable):
        return 'session_generation_unverified'
    if not sources:
        return 'source_missing'
    for source in sources:
        if type(source) is not dict or any(type(source.get(k)) is not type(v) or source.get(k) != v
                                         for k,v in SOURCE_CLASSIFICATION.items()):
            return 'source_identity_or_owner_conflict'
    return 'ok'


@dataclass(frozen=True)
class HallModel:
    items: object
    ordinary: tuple
    ordinary_p: tuple
    event_p: tuple
    season_p: tuple
    quality_mass: tuple


def load_model(read_bytes):
    """Exact plaintext callback works with source and transparent decryption.

    Every invocation checks inputs; no stale cross-session/PID cache exists.
    """
    blobs = {}
    for path,pin in INPUTS.items():
        try:
            raw = read_bytes(path)
        except Exception as error:
            raise ModelInputError('model_input_missing:' + path) from error
        if type(raw) is not bytes or (len(raw),hashlib.sha256(raw).hexdigest()) != pin:
            raise ModelInputError('model_input_identity:' + path)
        blobs[path] = raw
    rows = list(iter_table_rows(decode_table_text(blobs['data/raw/tables/Item.txt'].decode('ascii'))))
    parsed = parse_item_table(rows)
    items = {i: MappingProxyType(dict(quality=r.quality, value=r.value,
              cells=r.shape_w*r.shape_h, shape_code=r.shape_w*10+r.shape_h,
              tags=tuple(r.tags))) for i,r in parsed.items()}
    hall = {i for i,r in parsed.items() if json.loads(r.raw_row[39]) == [601]}
    if hall != set(EVENT_IDS) | set(SEASON_IDS) or set(EVENT_IDS) & set(SEASON_IDS):
        raise ModelInputError('guarantee_set_identity')
    weights = json.loads(blobs['data/processed/drop_weights_all_maps_0627.json'])
    ordinary = tuple(sorted(map(int,weights['2601']['items'])))
    digest = hashlib.sha256(json.dumps(ordinary,separators=(',',':')).encode('ascii')).hexdigest()
    if len(ordinary) != 675 or digest != 'b2f17ed59d2aa2e1b1000414ad6444467297f13202a70fb4df983002746b8f3e':
        raise ModelInputError('ordinary_support_identity')
    rankrows = iter_table_rows(decode_table_text(blobs['data/raw/tables/RankMap.txt'].decode('ascii')))
    anchors = json.loads(next(r for r in rankrows if r[0]=='6001')[6])
    masses = json.loads(blobs['data/processed/drop_table_priors_0627.json'])['2601']['quality_probs']
    p = _probabilities_for_support(support=ordinary,items=items,anchors=anchors,quality_probs=masses)
    def group_p(ids):
        if not ids or any(items[i]['quality'] != 6 or items[i]['value']<=0 or items[i]['cells']<=0 for i in ids):
            raise ModelInputError('invalid_guarantee_support')
        values = [_col6_weight(anchors,items[i]['value']) for i in ids]
        return tuple(v/sum(values) for v in values)
    return HallModel(MappingProxyType(items),ordinary,tuple(p[i] for i in ordinary),
                     group_p(EVENT_IDS),group_p(SEASON_IDS),tuple(masses[q]/sum(masses.values()) for q in QUALITY_KEYS))


def observations(snapshot, domains=()):
    """Merge same-session physical IDs; absence never asserts completeness."""
    if scope_state(snapshot) != 'ok':
        raise ModelInputError('observation_scope:' + scope_state(snapshot))
    context = snapshot.get('ui_contract',{}).get('context',{})
    session = snapshot.get('session_id',context.get('session_id'))
    rounds = [o[k] for o in (snapshot,context) for k in ('round','action_round','observed_round') if k in o]
    if rounds and any(type(v) is not int or not 1 <= v <= 6 for v in rounds):
        raise ModelInputError('invalid_current_round')
    current_round = max(rounds,default=1)
    surfaces = list(snapshot.get('minimap_grid_items') or ())
    surfaces += list(snapshot.get('ui_contract',{}).get('minimap',{}).get('items') or ())
    surfaces += [d for d in domains if d.get('hard_eligible') is True]
    physical = {}
    for item in surfaces:
        if type(item) is not dict or item.get('session_id',session) != session:
            raise ModelInputError('observation_session_conflict')
        for key in ('round','state_round_index','observed_round'):
            if key in item and (type(item[key]) is not int or not 1<=item[key]<=current_round):
                raise ModelInputError('observation_future_or_invalid_round')
        runtime,local = item.get('runtime_id'),item.get('local_index')
        if runtime is not None:
            _uint(runtime,'runtime_id',2**64-1,1)
        if local is not None:
            _uint(local,'local_index',511)
        if runtime is None and local is None:
            raise ModelInputError('observation_identity_missing')
        aliases = [k for k,v in physical.items() if runtime is not None and v.get('runtime_id')==runtime
                   or local is not None and v.get('local_index')==local]
        key = aliases[0] if aliases else ('r',runtime) if runtime is not None else ('l',local)
        if len(aliases)>1:
            # A later record can explicitly bridge an earlier runtime-only
            # view and a local-only view. It proves one physical slot; retain
            # both sets of facts and reject genuine contradictory identities.
            target = physical[key]
            for alias in aliases[1:]:
                prior = physical.pop(alias)
                for field,value in prior.items():
                    if field in ('candidate_item_ids','required_categories','excluded_categories','excluded_qualities') and field in target:
                        target[field] = tuple(sorted(set(target[field]) & set(value))) if field=='candidate_item_ids' else tuple(sorted(set(target[field])|set(value)))
                    elif field in target and target[field]!=value:
                        raise MathematicalConflict('physical_observation_conflict:' + field)
                    else:
                        target[field] = value
        old = physical.setdefault(key,dict(session_id=session))
        values = {k:item[k] for k in ('runtime_id','local_index','item_id','quality','value','cells',
                    'candidate_item_ids','required_categories','excluded_categories','excluded_qualities') if k in item}
        shape = item.get('shape_code',item.get('shape_key'))
        if shape is not None:
            if type(shape) is str and len(shape)<=2 and re.fullmatch('[1-6][1-7]',shape):
                shape = int(shape)
            values['shape_code'] = shape
        for field,value in values.items():
            if value is None or value == '' or value == 0 and field not in ('value','local_index'):
                continue
            if field in ('candidate_item_ids','required_categories','excluded_categories','excluded_qualities'):
                if type(value) not in (tuple,list) or any(type(v) is not int or v<0 for v in value):
                    raise ModelInputError('invalid_observation_' + field)
                value = tuple(sorted(set(value)))
                if field in old:
                    value = tuple(sorted(set(old[field]) & set(value))) if field=='candidate_item_ids' else tuple(sorted(set(old[field])|set(value)))
            else:
                _uint(value,'observation_'+field,2**64-1 if field=='runtime_id' else 10**12)
                if field in old and old[field] != value:
                    raise MathematicalConflict('physical_observation_conflict:' + field)
            old[field] = value
    return tuple(physical.values())


def _get(evidence,key,default=None):
    return evidence.get(key,default) if type(evidence) is dict else getattr(evidence,key,default)


def _observation_candidates(model,obs,session):
    if type(obs) is not dict or obs.get('session_id') != session:
        raise ModelInputError('observation_session_conflict')
    potential = set(model.ordinary) | set(EVENT_IDS) | set(SEASON_IDS) | {1006001}
    ids = (obs['item_id'],) if obs.get('item_id') else obs.get('candidate_item_ids',tuple(sorted(potential)))
    if type(ids) not in (tuple,list) or not ids or any(type(i) is not int or i not in potential for i in ids):
        raise ModelInputError('observation_outside_potential_route')
    selected = []
    for i in ids:
        r = model.items[i]
        if any(field in obs and obs[field] != r[field] for field in ('quality','value','cells','shape_code')):
            continue
        if r['quality'] in obs.get('excluded_qualities',()):
            continue
        if set(obs.get('excluded_categories',())) & set(r['tags']):
            continue
        if not set(obs.get('required_categories',())).issubset(r['tags']):
            continue
        selected.append(i)
    if not selected:
        raise MathematicalConflict('public_object_has_no_catalog_candidate')
    return tuple(selected)


def _quality(i,model):
    return 'q'+str(max(1,model.items[i]['quality']) if model.items[i]['quality']!=2 else 1)


def _check_fields(e):
    dict_fields = ('fixed_counts','min_counts','quality_cells','quality_cell_floors','avg_cells',
        'avg_values','quality_values','quality_value_floors','split_counts','split_quality_cells','split_avg_cells',
        'quality_min_total_value_floors','quality_catalog_min_value_floors','shape_average_value_floors',
        'hard_public_avg_values')
    for field in dict_fields:
        value = _get(e,field,{}) or {}
        if type(value) is not dict:
            raise ModelInputError('invalid_' + field)
        allowed = ('white','green') if field.startswith('split_') else QUALITY_KEYS + (('total',) if field=='avg_cells' else ())
        for key,v in value.items():
            if key not in allowed or type(v) not in (int,float) or not math.isfinite(v) or v<0:
                raise ModelInputError('invalid_' + field)
            if field in ('fixed_counts','min_counts','split_counts'):
                _uint(v,field)
    for field in ('total_grid_target','public_category_total_value_floor','shape_average_subset_floor'):
        value = _get(e,field)
        if value is not None and (type(value) not in (int,float) or not math.isfinite(value) or value<0):
            raise ModelInputError('invalid_' + field)
    options = _get(e,'total_grid_options',()) or ()
    if type(options) not in (tuple,list) or any(type(v) is not int or v<0 for v in options):
        raise ModelInputError('invalid_total_grid_options')
    for field,width in (('quality_count_cells_options',2),('quality_count_cells_value_options',3),('quality_avg_value_intervals',2)):
        value = _get(e,field,{}) or {}
        if type(value) is not dict or any(q not in QUALITY_KEYS for q in value):
            raise ModelInputError('invalid_' + field)
        for q,options in value.items():
            options = (options,) if field=='quality_avg_value_intervals' else options
            if type(options) not in (tuple,list) or any(type(row) not in (tuple,list) or len(row)!=width
                    or any(type(v) not in (int,float) or not math.isfinite(v) or v<0 for v in row) for row in options):
                raise ModelInputError('invalid_' + field)
    for field in ('soft_avg_cell_keys','soft_avg_value_keys'):
        value = _get(e,field,()) or ()
        if type(value) not in (tuple,list,set,frozenset) or any(v not in QUALITY_KEYS for v in value):
            raise ModelInputError('invalid_' + field)
    for key,value in (_get(e,'count_sums',{}) or {}).items():
        qs = re.findall('q[13456]',key) if type(key) is str else []
        if not qs or ''.join(qs)!=key or len(qs)!=len(set(qs)):
            raise ModelInputError('unsupported_count_sum')
        _uint(value,'count_sum')
    sample = _get(e,'random_sample',{}) or {}
    if type(sample) is not dict or any(q not in QUALITY_KEYS or type(v) is not int or v<0 for q,v in sample.items()):
        raise ModelInputError('invalid_random_sample')
    for sample_n,value in (_get(e,'random_value_floors',()) or ()):
        _uint(sample_n,'random_value_floor_count')
        if type(value) not in (int,float) or not math.isfinite(value) or value<0:
            raise ModelInputError('invalid_random_value_floor')


def _prove_arithmetic(model,e,fixed,n,definite):
    """Only sound finite arithmetic proofs; sampling failure is not a proof."""
    minima = _get(e,'min_counts',{}) or {}
    for q,c in fixed.items():
        if c<minima.get(q,0):
            raise MathematicalConflict('fixed_quality_below_public_minimum')
        if c==0 and ((_get(e,'quality_cells',{}) or {}).get(q,0)>0 or (_get(e,'quality_values',{}) or {}).get(q,0)>0):
            raise MathematicalConflict('zero_quality_count_has_positive_subtotal')
        support = [model.items[i] for i in set(model.ordinary)|set(EVENT_IDS)|set(SEASON_IDS)|{1006001} if _quality(i,model)==q]
        cells = (_get(e,'quality_cells',{}) or {}).get(q)
        if cells is not None and not c*min(r['cells'] for r in support)<=cells<=c*max(r['cells'] for r in support):
            raise MathematicalConflict('quality_cells_outside_hard_count_bounds')
        known_value = sum(model.items[i]['value'] for i in definite if _quality(i,model)==q)
        value = (_get(e,'quality_values',{}) or {}).get(q)
        if value is not None and value<known_value:
            raise MathematicalConflict('public_quality_total_below_known_objects')
    if n is None:
        return
    floors = sum(max(fixed.get(q,0),minima.get(q,0)) for q in QUALITY_KEYS)
    if floors>n:
        raise MathematicalConflict('quality_minimum_conservation_conflict')
    known_cells = sum(model.items[i]['cells'] for i in definite)
    max_cell = max(model.items[i]['cells'] for i in set(model.ordinary)|set(EVENT_IDS)|set(SEASON_IDS)|{1006001})
    low = known_cells+n-len(definite)
    high = known_cells+(n-len(definite))*max_cell
    options = _get(e,'total_grid_options') or ((_get(e,'total_grid_target'),) if _get(e,'total_grid_target') is not None else ())
    if options and not any(low<=v<=high for v in options):
        raise MathematicalConflict('total_cells_outside_hard_count_bounds')


def _complete_quality_raw_values(model,e,fixed,n,domains):
    """Complete physical quality evidence is an equality, not a value floor.

    A public domain is priced only if all its candidates have the same raw
    value and canonical quality. Unknown same-quality slots keep their normal
    public subtotal/mean override. No sampled candidate becomes a hard fact.
    """
    known={q:0 for q in QUALITY_KEYS}; priced={q:0 for q in QUALITY_KEYS}
    totals={q:0 for q in QUALITY_KEYS}; ambiguous=set()
    for domain in domains:
        qualities={_quality(i,model) for i in domain}
        if len(qualities)!=1:
            ambiguous.update(qualities)
            continue
        q=next(iter(qualities)); known[q]+=1
        values={model.items[i]['value'] for i in domain}
        if len(values)==1:
            priced[q]+=1; totals[q]+=next(iter(values))
    complete={}
    for q in QUALITY_KEYS:
        count=fixed.get(q)
        if count is None and n is not None and all(other in fixed for other in QUALITY_KEYS if other!=q):
            count=n-sum(fixed[other] for other in QUALITY_KEYS if other!=q)
        if count is None and n==len(domains) and q not in ambiguous:
            count=known[q]
        if count is None or count!=known[q] or priced[q]!=count:
            continue
        actual=totals[q]
        targets=[]
        if q in (_get(e,'quality_values',{}) or {}):
            targets.append(_get(e,'quality_values')[q])
        if q in (_get(e,'hard_public_avg_values',{}) or {}):
            targets.append(count*_get(e,'hard_public_avg_values')[q])
        if q in (_get(e,'avg_values',{}) or {}) and q not in (_get(e,'soft_avg_value_keys',()) or ()):
            targets.append(count*_get(e,'avg_values')[q])
        # Absolute tolerance only covers multiply/divide float roundoff; the
        # reported price below remains the exact integer raw physical sum.
        if any(not math.isclose(target,actual,rel_tol=0,abs_tol=1e-6) for target in targets):
            raise MathematicalConflict('complete_quality_public_value_conflict:' + q)
        complete[q]=actual
    return complete


def _hard_match(e,counts,cells,values,split_n,split_c,n):
    logweight = 0.0
    for j,key in enumerate(QUALITY_KEYS):
        c,g,v = counts[j],cells[j],values[j]
        if key in (_get(e,'fixed_counts',{}) or {}) and c != _get(e,'fixed_counts')[key]:
            return None
        if c < (_get(e,'min_counts',{}) or {}).get(key,0):
            return None
        if g < (_get(e,'quality_cell_floors',{}) or {}).get(key,0):
            return None
        if key in (_get(e,'quality_cells',{}) or {}) and abs(g-_get(e,'quality_cells')[key])>1e-5:
            return None
        for field,quantity,softfield,scale in (('avg_cells',g,'soft_avg_cell_keys',.25),('avg_values',v,'soft_avg_value_keys',.3)):
            if key in (_get(e,field,{}) or {}):
                target = c*_get(e,field)[key]
                delta = quantity-target
                if key in (_get(e,softfield,()) or ()):
                    logweight -= .5*(delta/max(1,c*scale if field=='avg_cells' else target*scale))**2
                elif abs(delta)>max(.01,c*.005 if field=='avg_cells' else abs(target)*1e-6):
                    return None
        if key in (_get(e,'quality_values',{}) or {}) and abs(v-_get(e,'quality_values')[key])>.01:
            return None
        if key in (_get(e,'hard_public_avg_values',{}) or {}) and abs(v-c*_get(e,'hard_public_avg_values')[key])>.01:
            return None
        for field in ('quality_value_floors','quality_min_total_value_floors','quality_catalog_min_value_floors','shape_average_value_floors'):
            if v < (_get(e,field,{}) or {}).get(key,0):
                return None
        options = (_get(e,'quality_count_cells_options',{}) or {}).get(key)
        if options is not None and (int(c),int(g)) not in tuple(tuple(row) for row in options):
            return None
        options = (_get(e,'quality_count_cells_value_options',{}) or {}).get(key)
        if options is not None and not any(c==cn and g==cg and abs(v-cv)<.01 for cn,cg,cv in options):
            return None
        interval = (_get(e,'quality_avg_value_intervals',{}) or {}).get(key)
        if interval is not None and (not c or not interval[0]<=v/c<=interval[1]):
            return None
    for key,target in (_get(e,'count_sums',{}) or {}).items():
        qs = re.findall('q[13456]',key)
        if not qs or ''.join(qs)!=key or len(qs)!=len(set(qs)):
            raise ModelInputError('unsupported_count_sum')
        _uint(target,'count_sum')
        if sum(counts[QUALITY_KEYS.index(q)] for q in qs)!=target:
            return None
    for j,key in enumerate(('white','green')):
        for field,v in (('split_counts',split_n[j]),('split_quality_cells',split_c[j])):
            if key in (_get(e,field,{}) or {}) and v!=_get(e,field)[key]:
                return None
        if key in (_get(e,'split_avg_cells',{}) or {}) and abs(split_c[j]-split_n[j]*_get(e,'split_avg_cells')[key])>.01:
            return None
    grid = sum(cells)
    if _get(e,'total_grid_options'):
        if grid not in _get(e,'total_grid_options'):
            return None
    elif _get(e,'total_grid_target') is not None and abs(grid-_get(e,'total_grid_target'))>.01:
        return None
    if 'total' in (_get(e,'avg_cells',{}) or {}) and abs(grid-n*_get(e,'avg_cells')['total'])>max(.01,n*.005):
        return None
    if sum(values)<max(_get(e,'public_category_total_value_floor',0),_get(e,'shape_average_subset_floor',0)):
        return None
    for sample_n,floor in (_get(e,'random_value_floors',()) or ()):
        if n<sample_n or sum(values)<floor:
            return None
    sample = _get(e,'random_sample',{}) or {}
    if sample:
        sample_n = sum(sample.values())
        if any(key not in QUALITY_KEYS or type(v) is not int or v<0 for key,v in sample.items()):
            raise ModelInputError('invalid_random_sample')
        if sample_n>n or any(sample.get(q,0)>counts[j] for j,q in enumerate(QUALITY_KEYS)):
            return None
        logweight += sum(math.log(math.comb(int(counts[j]),sample.get(q,0))) for j,q in enumerate(QUALITY_KEYS))-math.log(math.comb(n,sample_n))
    return logweight


def posterior(model,evidence,observed=(),*,snapshot,seed=6001,draws=4096,count_prior=None):
    """Bounded conditional particles; exhaustion is not a feasibility proof."""
    state = scope_state(snapshot)
    if state!='ok':
        raise ModelInputError('scope:' + state)
    if type(model) is not HallModel:
        raise ModelInputError('invalid_model')
    live_phase = snapshot.get('phase',snapshot.get('ui_contract',{}).get('context',{}).get('phase'))
    if type(_get(evidence,'map_id',6001)) is not int or _get(evidence,'map_id',6001) != 6001 or _get(evidence,'phase',live_phase) != live_phase:
        raise ModelInputError('evidence_scope_conflict')
    _uint(draws,'draws',65536,1)
    _uint(seed,'seed',2**64-1)
    _check_fields(evidence)
    if any(str(v).startswith('hard_conflict:') for v in (_get(evidence,'source_notes',()) or ())):
        raise MathematicalConflict('public_constraints_conflict')
    canonical = observations(snapshot,_get(evidence,'physical_candidate_domains',()) or ())
    # Re-merge caller domains/observations rather than counting two surfaces.
    merged_snapshot = dict(snapshot,minimap_grid_items=list(canonical)+list(observed))
    observed = observations(merged_snapshot)
    session = snapshot.get('session_id',snapshot.get('ui_contract',{}).get('context',{}).get('session_id'))
    domains = [_observation_candidates(model,o,session) for o in observed]
    fixed = _get(evidence,'fixed_counts',{}) or {}
    n_exact = _get(evidence,'total_count')
    if n_exact is None and set(fixed)==set(QUALITY_KEYS):
        n_exact = sum(fixed.values())
    if n_exact is not None:
        _uint(n_exact,'total_count')
        if n_exact<2:
            raise MathematicalConflict('two_guarantees_require_two_slots')
        if len(observed)>n_exact or sum(fixed.values())>n_exact or sum((_get(evidence,'min_counts',{}) or {}).values())>n_exact:
            raise MathematicalConflict('hard_count_arithmetic_conflict')
        if set(fixed)==set(QUALITY_KEYS) and sum(fixed.values())!=n_exact:
            raise MathematicalConflict('quality_count_conservation_conflict')
    if fixed.get('q6',2)<2:
        raise MathematicalConflict('two_guarantees_require_two_reds')
    definite = [d[0] for d in domains if len(d)==1]
    missing_definite = int(not any(i in EVENT_IDS for i in definite))+int(not any(i in SEASON_IDS for i in definite))
    if n_exact is not None and len(definite)+missing_definite>n_exact:
        raise MathematicalConflict('known_objects_and_missing_guarantees_exceed_total')
    for q,c in fixed.items():
        if sum(_quality(i,model)==q for i in definite)>c:
            raise MathematicalConflict('known_objects_exceed_quality_count')
    _prove_arithmetic(model,evidence,fixed,n_exact,definite)
    complete_quality_values=_complete_quality_raw_values(model,evidence,fixed,n_exact,domains)
    prior = count_prior if count_prior is not None else COUNT_PRIOR
    metadata = dict(schema=MODEL_VERSION,generation=GENERATION,map_id=6001,
        official_distribution_authority=False,replay_eligible=False,current_training_claim=False,
        base_prior_source='current ordinary2601 quality mass + RankMap6001 col6',
        value_source='current raw Item catalog; exact public subtotals override sampled estimates',guarantee_event_rows=18,guarantee_season_rows=27,
        observed_physical_count=len(observed),count_prior_used=n_exact is None,
        assumptions=['hidden replacement draws','at-least-one each set; disclosed extras replace ordinary slots'],
        attempts=draws,accepted=0,guarantee_violations=0,range_quantiles=[.1,.5,.9])
    if n_exact is None and prior is None:
        return dict(**metadata,status='computational_insufficient',reason='warehouse_count_prior_unavailable',value_range=None,value_quartiles=None,count_std=None)
    ns = np.arange(max(2,len(observed)),513) if n_exact is None else np.array([n_exact])
    if n_exact is None:
        if type(prior) not in (dict,MappingProxyType) or any(type(prior.get(k)) not in (int,float) or not math.isfinite(prior[k]) for k in ('center','sigma')) or prior['sigma']<=0 or not prior.get('source'):
            raise ModelInputError('invalid_count_prior')
        logs = -.5*((ns-prior['center'])/prior['sigma'])**2
        probs = np.exp(logs-logs.max()); probs /= probs.sum()
        metadata['count_prior'] = dict(prior)
    else:
        probs = [1]
    rng = np.random.default_rng(seed)
    counts_out,cells_out,values_out,ns_out,weights = [],[],[],[],[]
    pmap = dict(zip(model.ordinary,model.ordinary_p))
    pmap.update(zip(EVENT_IDS,model.event_p)); pmap.update(zip(SEASON_IDS,model.season_p))
    base_by_q = {q:tuple(i for i in model.ordinary if _quality(i,model)==q) for q in QUALITY_KEYS}
    base_p = {q:np.array([pmap[i] for i in base_by_q[q]]) for q in QUALITY_KEYS}
    for q in QUALITY_KEYS:
        base_p[q] /= base_p[q].sum()
    for n in rng.choice(ns,size=draws,p=probs):
        n = int(n)
        chosen = []
        for domain in domains:
            # Fixed IDs are exact. Partial public domains use disclosed model
            # weights, not a settlement-derived identification.
            dp = np.array([pmap.get(i,1/675) for i in domain],float); dp /= dp.sum()
            chosen.append(int(rng.choice(domain,p=dp)))
        seen_event = sum(i in EVENT_IDS for i in chosen)
        seen_season = sum(i in SEASON_IDS for i in chosen)
        missing = int(not seen_event)+int(not seen_season)
        if len(chosen)+missing>n:
            continue
        if not seen_event:
            chosen.append(int(rng.choice(EVENT_IDS,p=model.event_p)))
        if not seen_season:
            chosen.append(int(rng.choice(SEASON_IDS,p=model.season_p)))
        known_counts = np.array([sum(_quality(i,model)==q for i in chosen) for q in QUALITY_KEYS])
        residual = n-len(chosen)
        allocated = np.zeros(5,dtype=int)
        valid = True
        for j,q in enumerate(QUALITY_KEYS):
            if q in fixed:
                allocated[j] = fixed[q]-known_counts[j]
                if allocated[j]<0:
                    valid = False
        free = [j for j,q in enumerate(QUALITY_KEYS) if q not in fixed]
        left = residual-int(allocated.sum())
        if not valid or left<0 or not free and left:
            continue
        logcondition = math.lgamma(residual+1)-math.lgamma(left+1)
        for j,q in enumerate(QUALITY_KEYS):
            if q in fixed:
                logcondition -= math.lgamma(int(allocated[j])+1)
                logcondition += int(allocated[j])*math.log(model.quality_mass[j])
        if free:
            mass = sum(model.quality_mass[j] for j in free)
            logcondition += left*math.log(mass)
            allocated[free] = rng.multinomial(left,[model.quality_mass[j]/mass for j in free])
        for j,q in enumerate(QUALITY_KEYS):
            chosen.extend(map(int,rng.choice(base_by_q[q],size=int(allocated[j]),p=base_p[q])))
        counts = np.zeros(5,dtype=int); cells = np.zeros(5,dtype=int); values = np.zeros(5,dtype=float)
        split_n = np.zeros(2,dtype=int); split_c = np.zeros(2,dtype=int)
        for i in chosen:
            r = model.items[i]; j = QUALITY_KEYS.index(_quality(i,model))
            counts[j]+=1; cells[j]+=r['cells']; values[j]+=r['value']
            if r['quality'] in (1,2):
                split_n[r['quality']-1]+=1; split_c[r['quality']-1]+=r['cells']
        known_values = np.zeros(5)
        for i in chosen[:len(observed)]:
            known_values[QUALITY_KEYS.index(_quality(i,model))]+=model.items[i]['value']
        for j,q in enumerate(QUALITY_KEYS):
            if q in complete_quality_values:
                values[j]=complete_quality_values[q]
            elif q in (_get(evidence,'quality_values',{}) or {}):
                values[j]=_get(evidence,'quality_values')[q]
            elif q in (_get(evidence,'hard_public_avg_values',{}) or {}):
                values[j]=counts[j]*_get(evidence,'hard_public_avg_values')[q]
            elif q in (_get(evidence,'avg_values',{}) or {}) and q not in (_get(evidence,'soft_avg_value_keys',()) or ()):
                values[j]=counts[j]*_get(evidence,'avg_values')[q]
        if np.any(values<known_values):
            continue
        likelihood = _hard_match(evidence,counts,cells,values,split_n,split_c,n)
        if likelihood is None:
            continue
        assert sum(counts)==n and len(chosen)==n and any(i in EVENT_IDS for i in chosen) and any(i in SEASON_IDS for i in chosen)
        counts_out.append(counts); cells_out.append(cells); values_out.append(values); ns_out.append(n)
        weights.append(likelihood+logcondition)
    metadata['accepted'] = len(weights)
    if not weights:
        return dict(**metadata,status='computational_insufficient',reason='bounded_sampling_exhausted_for_constraints',value_range=None,value_quartiles=None,count_std=None)
    w = np.exp(np.array(weights)-max(weights)); w /= w.sum()
    counts,cells,values = map(np.array,(counts_out,cells_out,values_out))
    count_mean=float(np.dot(w,ns_out))
    count_std=float(np.sqrt(np.dot(w,(np.asarray(ns_out,dtype=float)-count_mean)**2)))
    def ranges(x,quantiles=(.1,.5,.9)):
        order = np.argsort(x,kind='stable'); xx=np.asarray(x)[order]; cw=np.cumsum(w[order])
        return [float(xx[min(len(xx)-1,int(np.searchsorted(cw,p)))]) for p in quantiles]
    return dict(**metadata,status='ok',reason='algorithm_model',effective_samples=float(1/np.dot(w,w)),
        value_range=ranges(values.sum(axis=1)),value_quartiles=ranges(values.sum(axis=1),(.25,.5,.75)),
        total_count_range=ranges(ns_out),total_grid_range=ranges(cells.sum(axis=1)),
        quality_count_ranges={q:ranges(counts[:,j]) for j,q in enumerate(QUALITY_KEYS)},
        quality_cells_ranges={q:ranges(cells[:,j]) for j,q in enumerate(QUALITY_KEYS)},
        quality_value_ranges={q:ranges(values[:,j]) for j,q in enumerate(QUALITY_KEYS)},
        quality_count_means={q:float(np.dot(w,counts[:,j])) for j,q in enumerate(QUALITY_KEYS)},
        quality_cells_means={q:float(np.dot(w,cells[:,j])) for j,q in enumerate(QUALITY_KEYS)},
        quality_value_means={q:float(np.dot(w,values[:,j])) for j,q in enumerate(QUALITY_KEYS)},
        count_mean=count_mean,count_std=count_std,cells_mean=float(np.dot(w,cells.sum(axis=1))),
        value_mean=float(np.dot(w,values.sum(axis=1))))


__all__ = ('GENERATION','MODEL_VERSION','INPUTS','SOURCE_CLASSIFICATION','EVENT_IDS','SEASON_IDS','COUNT_PRIOR',
           'ModelInputError','MathematicalConflict','scope_state','load_model','observations','posterior')
