"""Explicit 5215 economic-peer model, separate from official drop authority.

The distribution is conditioned per N on the activity guarantees. Observations
occupy physical slots: revealed values replace those slots, never add a second
antique warehouse. Exact public constraints consume RefEvidence from the public
engine parser. Bounded sampling exhaustion is distinct from a proven conflict.
"""
from __future__ import annotations

from functools import lru_cache
import hashlib
import json
import math
import re
from pathlib import Path

import numpy as np

GENERATION = 'b049c3-2026-09-10-597'
CURRENT_GENERATION = '76b7ca-2026-10-08-723'
MODEL_VERSION = 'wuqilin_5215_peer_conditioned_v1'
COUNT_PRIOR = {
    'center': 58.728813559322035, 'sigma': 5.384628295417902, 'train_sessions': 177,
    'candidate_sha256': '72478886d09434b7b07ae0014123e0f225f50ea2df03e05d51da0dd29f697a77',
    'input_sha256': 'bdff22d3a43414eadc54a49834a9ae53ed40388189e17d16841d9ca6288ede4d',
    'split_policy_sha256': '9e516fbbe4bedddf87c08f6dbd9e50c53260c34230173f9526271cafe2dc5a15',
    'generation': GENERATION,
    'qualification': 'train-only descriptive settlement prior; upload-reclassified provenance unresolved',
    'gaussian_shape': 'model_assumption', 'official_distribution_eligible': False,
    'replay_eligible': False, 'tuning_pool_eligible': False,
}
QUALITY_KEYS = ('q1', 'q3', 'q4', 'q5', 'q6')
INPUTS = {
    'data/processed/items_droppable.json': (357508, '3d0eb1a17de6f433b7bebc01eb753f81b983060bfaa7ba0a10c0f88662926c98'),
    'data/processed/drop_weights_all_maps_0627.json': (2664405, 'ce0db9680b53e393bc7a0db9e89e668ff3674701bdd524ed3b000f822bc8df11'),
    'data/processed/drop_table_priors_0627.json': (21239, 'b9461153ccd18f82833b238f92f8599496ddc03977e889ef514b5932c870fe15'),
}
INPUTS_036 = {
    'data/processed/items_droppable.json': (322888, '68e944020749968d301dca61ff2b12fccd21b32d93c22b72d394285cd9658f91'),
    'data/processed/drop_weights_all_maps_0627.json': (2556143, 'b7c55c9ee0a8b9211b4a70f53f1c67d4e3f037d4a2faa8e7550e1f240d4499d3'),
    'data/processed/drop_table_priors_0627.json': (21239, '83f2d76cfffb907fd3cd5116f9dcde880112255b818ea1e9dafe361eab901ef9'),
}
PROFILE_BUILDS = {GENERATION: '25221911', CURRENT_GENERATION: '25795126'}


class ModelInputError(ValueError):
    pass


def scope_state(snapshot, *, expected_generation=GENERATION):
    if type(expected_generation) is not str or expected_generation not in PROFILE_BUILDS:
        return 'generation_conflict'
    generation = expected_generation
    if type(snapshot) is not dict:
        return 'invalid_owner'
    owners = [snapshot]
    ui = snapshot.get('ui_contract', {})
    if type(ui) is not dict:
        return 'invalid_owner'
    for key in ('context', 'source'):
        owner = ui.get(key, {})
        if type(owner) is not dict:
            return 'invalid_owner'
        owners.append(owner)
    maps, sessions, phases, sources, stable, heroes, epochs = [], [], [], [], [], [], []
    for owner in owners:
        for key, values in (('map_id', maps), ('session_id', sessions), ('phase', phases),
                            ('warehouse_source', sources), ('warehouse_game_version_session_stable', stable)):
            if key in owner:
                values.append(owner[key])
        for key in ('hero_id', 'player_hero_id', 'current_player_hero_id'):
            if key in owner and owner[key] is not None:
                heroes.append(owner[key])
        if owner.get('hero') not in (None, '', 'wuqilin', '吴起灵', '207', 207):
            return 'hero_conflict'
        for key in ('model_map_id', 'physical_catalog_map_id'):
            if owner.get(key) is not None and (type(owner[key]) is not int or owner[key] != 5215):
                return 'map_alias_conflict'
        if owner.get('map_alias') or owner.get('map_alias_mode'):
            return 'map_alias_conflict'
        for key in ('session_epoch', 'epoch'):
            if key in owner:
                epochs.append((key, owner[key]))
        if owner.get('formal_mode_reason') == 'game_generation_unverified':
            return 'game_generation_unverified'
        if owner.get('warehouse_source_generation', generation) != generation:
            return 'generation_conflict'
    if not maps or any(type(x) not in (int, str) or x not in (5215, '5215') for x in maps):
        return 'map_conflict'
    if not sessions or any(type(x) is not str or not x.startswith('5215:') or not x[5:].isascii()
        or not x[5:].isdecimal() or x != sessions[0] for x in sessions):
        return 'session_conflict'
    tail = sessions[0][5:]
    if len(tail) > 20 or not re.fullmatch(r'[1-9][0-9]*', tail) or int(tail) > 2**64 - 1:
        return 'session_conflict'
    if heroes and any(type(x) is not int or x != 207 for x in heroes):
        return 'hero_conflict'
    if not heroes and not any(owner.get('hero') in ('wuqilin', '吴起灵') for owner in owners):
        return 'hero_missing'
    if not phases or any(type(x) is not str or x not in ('reading', 'bidding') or x != phases[0] for x in phases):
        return 'not_live_pre_settlement'
    if not stable or any(x is not True for x in stable):
        return 'session_generation_unverified'
    for _key, value in epochs:
        if type(value) is int:
            valid = 0 < value <= 2**64 - 1 and value == int(tail)
        elif type(value) is str:
            valid = value == tail
        else:
            valid = False
        if not valid:
            return 'epoch_conflict'
    if not sources:
        return 'source_missing'
    for source in sources:
        if type(source) is not dict or type(source.get('map_id')) is not int or source['map_id'] != 5215:
            return 'source_conflict'
        if any(source.get(key) != generation for key in ('source_generation', 'map_profile_generation', 'game_version')):
            return 'generation_conflict'
        if source.get('source_conflict') is not False or source.get('quarantine_required') is not False:
            return 'source_conflict'
        expected = {
            'qualification':'official_random_warehouse', 'reason':'pw2_sealed_owner_block_absent',
            'table_status':'verified', 'explicit_source':'', 'map_table_build':PROFILE_BUILDS[generation],
            'auction_mode':'sealed', 'owner_evidence_status':'absent',
            'map_table_sha256':'7abe97f33ec48c8b18887de5abcddc1969b5decc2b9be30b1f8fd73f5b71c9fc',
            'expected_map_table_sha256':'7abe97f33ec48c8b18887de5abcddc1969b5decc2b9be30b1f8fd73f5b71c9fc',
            'map_source_contract_sha256':'b6e0635f8033bb20dbac7a6c6a985fe5bd4d923da1bf9a2d3d21dd524273a6f3',
            'expected_map_source_contract_sha256':'b6e0635f8033bb20dbac7a6c6a985fe5bd4d923da1bf9a2d3d21dd524273a6f3',
        }
        if any(type(source.get(key)) is not str or source[key] != value for key,value in expected.items()):
            return 'source_identity_conflict'
        if source.get('config') is not None:
            return 'owner_config_conflict'
    return 'ok'


def input_profile(generation=GENERATION):
    if type(generation) is not str or generation not in PROFILE_BUILDS:
        raise ModelInputError('model_generation_unsupported')
    return INPUTS_036 if generation == CURRENT_GENERATION else INPUTS


def load_model(read_bytes, *, generation=GENERATION):
    """Verify current plaintext before entering caches, including encrypted loaders."""
    blobs = []
    for relative, expected in input_profile(generation).items():
        try:
            raw = read_bytes(relative)
        except Exception as error:
            raise ModelInputError('model_input_missing:' + relative) from error
        if (len(raw), hashlib.sha256(raw).hexdigest()) != expected:
            raise ModelInputError('model_input_identity:' + relative)
        blobs.append(raw)
    return _decode_model(*blobs)


@lru_cache(maxsize=1)
def _decode_model(catalog_bytes, weights_bytes, priors_bytes):
    catalog = {row['item_id']: row for row in json.loads(catalog_bytes)}
    weights, priors = json.loads(weights_bytes), json.loads(priors_bytes)
    peers = []
    for peer_id in range(2501, 2511):
        entries = weights[str(peer_id)]['items']
        ids = np.array(sorted(map(int, entries)), dtype=np.int64)
        raw_q = np.array([max(1, catalog[int(i)]['quality']) for i in ids])
        q = raw_q.copy()
        q[q == 2] = 1
        cells = np.array([catalog[int(i)]['shape_w'] * catalog[int(i)]['shape_h'] for i in ids])
        shape = np.array([catalog[int(i)]['shape_w'] * 10 + catalog[int(i)]['shape_h'] for i in ids])
        antique = np.array([106 in catalog[int(i)]['tags'] for i in ids])
        kind = np.where(antique, np.where(q == 6, 0, 1), 2)
        p = np.array([entries[str(i)][0] for i in ids], dtype=float)
        p /= p.sum()
        value = np.array([entries[str(i)][2] for i in ids], dtype=float)
        for quality in (1, 3, 4, 5, 6):
            mask = q == quality
            mean = np.dot(p[mask], value[mask]) / p[mask].sum()
            value[mask] *= priors[str(peer_id)]['quality_values']['q' + str(quality)] / mean
        peers.append(dict(id=peer_id, ids=ids, q=q, cells=cells, shape=shape,
                          raw_q=raw_q,
                          tags=tuple(frozenset(catalog[int(i)]['tags']) for i in ids),
                          kind=kind, p=p, value=value,
                          masses=np.array([p[kind == k].sum() for k in range(3)])))
    return peers


def _integer(value, field, *, minimum=0, maximum=512):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ModelInputError('invalid_' + field)
    return value


def observations(snapshot, rows, domains=()):
    """Merge same-session physical identities across R2/R3/R4 without inventing completeness."""
    context = snapshot.get('ui_contract', {}).get('context', {})
    session = snapshot.get('session_id', context.get('session_id'))
    current_round = max((owner.get(key,0) for owner in (snapshot,context)
                         for key in ('round','action_round','observed_round')
                         if type(owner.get(key,0)) is int),default=0)
    a_values, physical = set(), {}
    skills = {100207: 1, 10002071: 2, 10002072: 3, 4022: 4}
    for row in rows:
        if type(row) is not dict or row.get('skill_id') not in skills:
            continue
        if row.get('session_id') != session or type(row.get('hero_id')) is not int or row['hero_id'] != 207:
            raise ModelInputError('reveal_session_or_hero_conflict')
        expected_round = skills[row['skill_id']]
        if type(row.get('state_round_index')) is not int or row['state_round_index'] != expected_round:
            raise ModelInputError('reveal_round_conflict')
        if type(current_round) is int and current_round and expected_round > current_round:
            raise ModelInputError('reveal_future_round')
        if row.get('result') is not None and row['skill_id'] == 100207:
            a_values.add(_integer(row['result'], 'antique_count', minimum=2))
        for item in row.get('observed_items') or ():
            _merge_observation(physical, item, antique=True)
    if len(a_values) > 1:
        raise ModelInputError('antique_count_conflict')
    # The producer's minimap contains actual public and item reveals as well as
    # the skill rows above. Physical runtime identity unifies the two surfaces.
    for item in snapshot.get('minimap_grid_items') or ():
        _merge_observation(physical, item, antique=None)
    for item in snapshot.get('ui_contract', {}).get('minimap', {}).get('items') or ():
        _merge_observation(physical, item, antique=None)
    for domain in domains:
        if domain.get('hard_eligible') is not True:
            continue
        if not domain.get('candidate_item_ids'):
            raise ModelInputError('physical_domain_no_candidates')
        item={key:domain[key] for key in ('runtime_id','local_index','cells','shape_key',
            'required_categories','excluded_categories','excluded_qualities','candidate_item_ids') if key in domain}
        if domain.get('observed_quality') is not None:
            item['quality']=domain['observed_quality']
        if domain.get('exact_item_id') is not None:
            item.update(item_id=domain['exact_item_id'],value=domain['exact_value'])
        _merge_observation(physical,item,antique=True if 106 in domain.get('required_categories',()) else None)
    return next(iter(a_values), None), tuple(physical[key] for key in sorted(physical))


def _merge_observation(physical, item, *, antique):
    if type(item) is not dict:
        raise ModelInputError('invalid_observed_item')
    runtime = item.get('runtime_id')
    local = item.get('local_index')
    if type(runtime) is int and runtime > 0:
        key = 'r:' + str(runtime)
    elif type(local) is int and local >= 0:
        key = 'l:' + str(local)
    else:
        raise ModelInputError('observation_identity_missing')
    if type(local) is int and local >= 0:
        same_local=[old_key for old_key,row in physical.items() if row.get('local_index')==local]
        if len(same_local)==1 and same_local[0]!=key:
            prior=physical[same_local[0]]
            if prior.get('runtime_id') is not None and type(runtime) is int and prior['runtime_id']!=runtime:
                raise ModelInputError('physical_identity_conflict')
            if key.startswith('r:'):
                physical[key]=physical.pop(same_local[0])
                physical[key]['identity']=key
            else:
                key=same_local[0]
    old = physical.setdefault(key, dict(identity=key))
    if type(runtime) is int and runtime>0:
        old['runtime_id']=runtime
    if type(local) is int and local>=0:
        old['local_index']=local
    values = {k: item.get(k) for k in ('item_id', 'quality', 'value', 'cells', 'shape_code')}
    if values['shape_code'] is None:
        values['shape_code'] = item.get('shape_key')
        if type(values['shape_code']) is str and values['shape_code'].isascii() and values['shape_code'].isdecimal():
            values['shape_code'] = int(values['shape_code'])
    categories = item.get('categories') or ()
    category = item.get('category')
    if category == 106 or 106 in categories:
        antique = True
    required={value for value in categories if type(value) is int and 101<=value<=110}
    if type(category) is int and 101<=category<=110:
        required.add(category)
    if required:
        old['required_categories']=tuple(sorted(set(old.get('required_categories',()))|required))
    if antique is not None:
        values['antique'] = antique
    for field in ('excluded_qualities','excluded_categories','required_categories','candidate_item_ids'):
        raw=item.get(field)
        if raw:
            if type(raw) not in (list,tuple) or any(type(value) is not int for value in raw):
                raise ModelInputError('invalid_' + field)
            prior=set(old[field]) if field in old else None
            merged=(prior.intersection(raw) if field=='candidate_item_ids' and prior is not None
                    else set(old.get(field,()))|set(raw))
            old[field]=tuple(sorted(merged))
    for field, value in values.items():
        if value in (None, '', 0) and field not in ('value', 'antique'):
            continue
        if value is None or value == '':
            continue
        if field == 'antique':
            if type(value) is not bool:
                raise ModelInputError('invalid_antique_observation')
        else:
            _integer(value, 'observation_' + field, maximum=10**12)
        if field in old and old[field] != value:
            raise ModelInputError('physical_observation_conflict:' + field)
        old[field] = value


@lru_cache(maxsize=4096)
def _class_table(n, masses, actual_a=None):
    red, antique_other, _ = masses
    pa, pr = red + antique_other, red / (red + antique_other)
    states, weights = [], []
    valid_a=range(max(2,n//2+1),n+1)
    guarantee_mass=sum(math.comb(n,a)*pa**a*(1-pa)**(n-a)
        * (1-(1-pr)**a-a*pr*(1-pr)**(a-1)) for a in valid_a)
    for a in (valid_a if actual_a is None else (actual_a,) if actual_a in valid_a else ()):
        for r in range(2, a + 1):
            states.append((a, r))
            weights.append(math.comb(n, a) * pa**a * (1-pa)**(n-a)
                           * math.comb(a, r) * pr**r * (1-pr)**(a-r))
    probability = np.array(weights)
    probability /= guarantee_mass
    return np.array(states), probability


def _weighted_range(values, weights):
    order = np.argsort(values, kind='stable')
    ordered = values[order]
    cumulative = np.cumsum(weights[order])
    return tuple(int(round(ordered[min(len(order)-1, int(np.searchsorted(cumulative, p*cumulative[-1])))]))
                 for p in (.25, .5, .75))


@lru_cache(maxsize=8192)
def _binomial_probabilities(n, p):
    if p <= 0:
        return np.array([1.0]+[0.0]*n)
    if p >= 1:
        return np.array([0.0]*n+[1.0])
    return np.array([math.comb(n,k)*p**k*(1-p)**(n-k) for k in range(n+1)])


def _allocate_fixed_qualities(peer, remaining, observed_counts, fixed, rng):
    """Condition rare exact counts directly, rather than hope rejection hits them.

At each quality, X+Y=c is a convolution of the two remaining non-red-antique
and non-antique binomials. Its normalization is the importance likelihood;
the sampled X,Y split retains later constraints' correlation.
"""
    size=len(remaining)
    allocated=np.zeros((size,3,5),dtype=int)
    rem=np.maximum(remaining,0).copy()
    allocated[:,0,4]=rem[:,0]
    rem[:,0]=0
    logweight=np.zeros(size)
    allowed=np.ones(len(peer['ids']),dtype=bool)
    for qi,key in enumerate(QUALITY_KEYS):
        if key not in fixed:
            continue
        q=(1,3,4,5,6)[qi]
        targets=fixed[key]-observed_counts[:,qi]-allocated[:,:,qi].sum(axis=1)
        ps=[]
        for k in (1,2):
            denominator=peer['p'][allowed & (peer['kind']==k)].sum()
            numerator=peer['p'][allowed & (peer['kind']==k) & (peer['q']==q)].sum()
            ps.append(float(numerator/denominator) if denominator>0 else 0.0)
        triples=np.stack((rem[:,1],rem[:,2],targets),axis=1)
        for triple in np.unique(triples,axis=0):
            left,right,target=map(int,triple)
            owners=np.flatnonzero(np.all(triples==triple,axis=1))
            x=np.arange(max(0,target-right),min(left,target)+1)
            if not len(x):
                logweight[owners]=-np.inf
                continue
            pleft=_binomial_probabilities(left,ps[0])
            pright=_binomial_probabilities(right,ps[1])
            weights=pleft[x]*pright[target-x]
            mass=weights.sum()
            if mass<=0:
                logweight[owners]=-np.inf
                continue
            selected=rng.choice(x,len(owners),p=weights/mass)
            allocated[owners,1,qi]=selected
            allocated[owners,2,qi]=target-selected
            rem[owners,1]-=selected
            rem[owners,2]-=target-selected
            logweight[owners]+=math.log(mass)
        allowed &= peer['q'] != q
    return allocated,rem,allowed,logweight


def _quality_display_posterior(counts, cells, values, weight, evidence, observed):
    """Read-only projection of the *same* surviving weighted warehouse draws.

    Publish up to 64 likely joint states plus the actual pricing-quantile rows
    and dimension-quantile representatives. Omitted mass is never renormalized.
    Three whole-warehouse count arms come from actual price-quantile particles,
    not a Cartesian product of marginal quality counts.
    """
    order=np.argsort(values,kind='stable')
    cumulative=np.cumsum(weight[order])
    selected=[int(order[min(len(order)-1,int(np.searchsorted(cumulative,p*cumulative[-1])))]) for p in (.25,.5,.75)]
    joint,marginal_count,marginal_cells,locks,omitted={},{},{},{},{}
    exact_counts,exact_cells={},{}
    fully_observed=(evidence.total_count is not None and len(observed)==evidence.total_count
                    and all(row.get('quality') and (row.get('cells') or row.get('shape_code')) for row in observed))
    def histogram(matrix):
        states,inverse=np.unique(matrix,axis=0,return_inverse=True)
        mass=np.bincount(inverse,weights=weight,minlength=len(states))
        # Canonical floating-point summation, not a posterior reweighting.
        mass[int(np.argmax(mass))]+=1.0-math.fsum(map(float,mass))
        return states,mass
    for qi,key in enumerate(QUALITY_KEYS):
        pairs,mass=histogram(np.stack((counts[:,qi],cells[:,qi]),axis=1))
        chosen=set(map(int,np.argsort(-mass,kind='stable')[:64]))
        for index in selected:
            chosen.add(int(np.flatnonzero(np.all(pairs==[counts[index,qi],cells[index,qi]],axis=1))[0]))
        for dim,array in enumerate((counts[:,qi],cells[:,qi])):
            for value in _weighted_range(array,weight):
                matching=np.flatnonzero(pairs[:,dim]==value)
                if len(matching): chosen.add(int(matching[np.argmax(mass[matching])]))
        ordered=sorted(chosen,key=lambda index:(-mass[index],*pairs[index]))
        joint[key]=tuple((int(pairs[i,0]),int(pairs[i,1]),float(mass[i])) for i in ordered if mass[i]>0)
        omitted[key]=max(0.0,1.0-math.fsum(row[2] for row in joint[key]))
        for array,target in ((counts[:,qi],marginal_count),(cells[:,qi],marginal_cells)):
            support,p=histogram(array)
            target[key]=tuple((int(support[i]),float(p[i])) for i in np.argsort(-p,kind='stable') if p[i]>0)
        count_support=np.unique(counts[:,qi]);cells_support=np.unique(cells[:,qi])
        count_fact=key in evidence.fixed_counts or fully_observed
        cells_fact=key in evidence.quality_cells or fully_observed or evidence.fixed_counts.get(key)==0
        if count_fact and len(count_support)==1: exact_counts[key]=int(count_support[0])
        if cells_fact and len(cells_support)==1: exact_cells[key]=int(cells_support[0])
        exact=key in exact_counts and key in exact_cells
        locks[key]=dict(strength='exact' if exact else 'probabilistic',
                        reason='observed_exact' if exact else 'sampled_weighted_posterior')
    return dict(quality_count_cells_candidates=joint,quality_count_candidates=marginal_count,
                quality_cells_candidates=marginal_cells,quality_lock=locks,
                quality_count_arms=tuple(sorted(set(tuple(map(int,counts[index])) for index in selected)))
                    if evidence.total_count is not None else (),
                quality_display=dict(schema='wuqilin_5215_joint_posterior_v1',omitted_joint_mass=omitted,
                    exact_counts=exact_counts,exact_cells=exact_cells,representative_arms='actual_price_quantile_particles',
                    posterior='same_surviving_particles_and_weights',joint_limit=64))


def posterior(peers, evidence, actual_a, observed, *, seed=5215, draws=16384,
              count_center=COUNT_PRIOR['center'], count_sigma=COUNT_PRIOR['sigma']):
    """Deterministic importance posterior with hard/soft evidence kept separate."""
    n_exact = evidence.total_count
    if n_exact is not None:
        _integer(n_exact, 'total_count', minimum=2)
        ns = [n_exact]
    else:
        low=max(2,len(observed),actual_a or 0)
        high=min(512,2*actual_a-1) if actual_a is not None else min(512,max(100,low+int(8*count_sigma)))
        ns = list(range(low,high+1))
    if actual_a is not None and n_exact is not None and (actual_a > n_exact or actual_a * 2 <= n_exact):
        raise ModelInputError('antique_count_guarantee_conflict')
    if n_exact is not None and len(observed) > n_exact:
        raise ModelInputError('observed_count_exceeds_total')
    if any(str(note).startswith('hard_conflict:') for note in evidence.source_notes):
        raise ModelInputError('public_constraints_conflict')
    rng = np.random.default_rng(seed)
    components, component_mass = [], []
    prior_logs={n:-.5*((n-count_center)/count_sigma)**2 for n in ns}
    prior_peak=max(prior_logs.values(),default=0)
    for pi, peer in enumerate(peers):
        for n in ns:
            states, p = _class_table(n, tuple(peer['masses']),actual_a)
            if not len(p):
                continue
            mask = states[:, 0] == actual_a if actual_a is not None else np.ones(len(p), dtype=bool)
            known_a = sum(row.get('antique') is True for row in observed)
            mask &= states[:, 0] >= known_a
            if not mask.any():
                continue
            mass = p[mask].sum()
            n_mass = 1 if n_exact is not None else math.exp(prior_logs[n]-prior_peak)
            components.append((pi, n, states[mask], p[mask]/mass))
            component_mass.append(n_mass * mass)
    if not components or sum(component_mass) <= 0:
        raise ModelInputError('antique_count_guarantee_conflict')
    component_mass = np.array(component_mass)
    component_mass /= component_mass.sum()
    chosen = rng.choice(len(components), size=draws, p=component_mass)
    out_values = np.zeros((draws, 5))
    out_counts = np.zeros((draws, 5), dtype=int)
    out_cells = np.zeros((draws, 5), dtype=int)
    known_values = np.zeros((draws, 5))
    split_counts = np.zeros((draws, 2), dtype=int)
    split_cells = np.zeros((draws, 2), dtype=int)
    out_a, out_r, out_n = (np.zeros(draws, dtype=int) for _ in range(3))
    logweight = np.zeros(draws)
    for ci in np.unique(chosen):
        indices = np.flatnonzero(chosen == ci)
        pi, n, states, prob = components[int(ci)]
        peer = peers[pi]
        state = states[rng.choice(len(states), size=len(indices), p=prob)]
        a, r = state[:,0], state[:,1]
        remaining = np.stack((r, a-r, n-a), axis=1)
        out_a[indices], out_r[indices], out_n[indices] = a, r, n
        # Each known physical object consumes one compatible slot. The item's
        # likelihood updates the peer posterior even when its price is exact.
        for obs in observed:
            mask = np.ones(len(peer['ids']), dtype=bool)
            if obs.get('antique') is True:
                mask &= peer['kind'] != 2
            if obs.get('quality'):
                mask &= peer['raw_q'] == obs['quality']
            if obs.get('excluded_qualities'):
                mask &= ~np.isin(peer['raw_q'],obs['excluded_qualities'])
            if obs.get('excluded_categories'):
                mask &= np.array([not set(obs['excluded_categories']).intersection(tags) for tags in peer['tags']])
            if obs.get('required_categories'):
                mask &= np.array([set(obs['required_categories']).issubset(tags) for tags in peer['tags']])
            if 'candidate_item_ids' in obs:
                mask &= np.isin(peer['ids'],obs['candidate_item_ids'])
            if obs.get('cells'):
                mask &= peer['cells'] == obs['cells']
            if obs.get('shape_code'):
                shape = obs['shape_code']
                mask &= (peer['shape'] == shape) | (peer['shape'] == (shape%10)*10+shape//10)
            if obs.get('item_id') in peer['ids']:
                mask &= peer['ids'] == obs['item_id']
            masses = np.array([peer['p'][mask & (peer['kind']==k)].sum()/peer['masses'][k] for k in range(3)])
            slot_mass = remaining * masses
            totals = slot_mass.sum(axis=1)
            valid = totals > 0
            eligible_slots = remaining[:,:2].sum(axis=1) if obs.get('antique') is True else remaining.sum(axis=1)
            logweight[indices] += np.log(np.maximum(totals / np.maximum(1, eligible_slots), 1e-300))
            logweight[indices[~valid]] = -np.inf
            kind_choice = (rng.random(len(indices))[:,None] * np.maximum(totals,1e-300)[:,None] > np.cumsum(slot_mass,axis=1)).sum(axis=1)
            kind_choice = np.minimum(kind_choice, 2)
            remaining[np.arange(len(indices)),kind_choice] -= valid
            for k in range(3):
                slots = np.flatnonzero((kind_choice==k) & valid)
                candidates = np.flatnonzero(mask & (peer['kind']==k))
                if not len(slots) or not len(candidates):
                    continue
                probabilities = peer['p'][candidates]
                selected = rng.choice(candidates,len(slots),p=probabilities/probabilities.sum())
                for quality_index, quality in enumerate((1,3,4,5,6)):
                    qmask = peer['q'][selected] == quality
                    owners = indices[slots[qmask]]
                    out_counts[owners,quality_index] += 1
                    out_cells[owners,quality_index] += obs.get('cells',peer['cells'][selected[qmask]])
                    out_values[owners,quality_index] += obs.get('value',peer['value'][selected[qmask]])
                    if 'value' in obs:
                        known_values[owners,quality_index] += obs['value']
                for split_index,quality in enumerate((1,2)):
                    qmask = peer['raw_q'][selected] == quality
                    owners = indices[slots[qmask]]
                    split_counts[owners,split_index] += 1
                    split_cells[owners,split_index] += obs.get('cells',peer['cells'][selected[qmask]])
        allocated, remaining, free_mask, count_likelihood = _allocate_fixed_qualities(
            peer,remaining,out_counts[indices],evidence.fixed_counts,rng)
        logweight[indices] += count_likelihood
        groups = [(k,qi,allocated[:,k,qi]) for k in range(3) for qi in range(5)]
        groups += [(k,None,remaining[:,k]) for k in range(3)]
        for k,forced_quality,quantity in groups:
            quantity = np.maximum(quantity,0)
            owners = np.repeat(indices,quantity)
            if not len(owners):
                continue
            mask=(peer['kind']==k)
            mask &= free_mask if forced_quality is None else peer['q']==(1,3,4,5,6)[forced_quality]
            candidates = np.flatnonzero(mask)
            if not len(candidates):
                logweight[np.unique(owners)] = -np.inf
                continue
            probabilities = peer['p'][candidates]
            selected = rng.choice(candidates,len(owners),p=probabilities/probabilities.sum())
            for qi,q in enumerate((1,3,4,5,6)):
                mask = peer['q'][selected] == q
                out_counts[:,qi] += np.bincount(owners[mask],minlength=draws)
                out_cells[:,qi] += np.bincount(owners[mask],weights=peer['cells'][selected[mask]],minlength=draws).astype(int)
                out_values[:,qi] += np.bincount(owners[mask],weights=peer['value'][selected[mask]],minlength=draws)
            for qi,q in enumerate((1,2)):
                mask = peer['raw_q'][selected] == q
                split_counts[:,qi] += np.bincount(owners[mask],minlength=draws)
                split_cells[:,qi] += np.bincount(owners[mask],weights=peer['cells'][selected[mask]],minlength=draws).astype(int)
    valid = np.isfinite(logweight)
    for qi,key in enumerate(QUALITY_KEYS):
        count,cells = out_counts[:,qi],out_cells[:,qi]
        if key in evidence.fixed_counts:
            valid &= count == evidence.fixed_counts[key]
        valid &= count >= evidence.min_counts.get(key,0)
        valid &= cells >= evidence.quality_cell_floors.get(key,0)
        if key in evidence.quality_cells:
            valid &= np.abs(cells-evidence.quality_cells[key]) < 1e-5
        if key in evidence.avg_cells:
            delta = cells-count*evidence.avg_cells[key]
            if key in evidence.soft_avg_cell_keys:
                logweight -= .5*(delta/np.maximum(1,count*.25))**2
            else:
                valid &= np.abs(delta) <= np.maximum(.01,count*.005)
        if key in evidence.quality_values:
            out_values[:,qi] = evidence.quality_values[key]
        elif key in evidence.avg_values:
            target=count*evidence.avg_values[key]
            if key in evidence.soft_avg_value_keys:
                logweight -= .5*((out_values[:,qi]-target)/np.maximum(1,target*.3))**2
            else:
                out_values[:,qi] = target
        if key in evidence.hard_public_avg_values:
            out_values[:,qi] = count*evidence.hard_public_avg_values[key]
        valid &= out_values[:,qi] >= known_values[:,qi]
        for floor in (evidence.quality_value_floors,evidence.quality_min_total_value_floors or {},
                      evidence.quality_catalog_min_value_floors or {}, evidence.shape_average_value_floors):
            valid &= out_values[:,qi] >= floor.get(key,0)
        options = (evidence.quality_count_cells_options or {}).get(key)
        if options:
            valid &= np.array([(int(n),int(c)) in options for n,c in zip(count,cells)])
        value_options = (evidence.quality_count_cells_value_options or {}).get(key)
        if value_options:
            candidates = {}
            for cn,cc,cv in value_options:
                candidates.setdefault((cn,cc),[]).append(cv)
            for index in np.flatnonzero(valid):
                options = candidates.get((int(count[index]),int(cells[index])),())
                if not options:
                    valid[index] = False
                else:
                    out_values[index,qi] = options[int(rng.integers(len(options)))]
                    valid[index] &= out_values[index,qi] >= known_values[index,qi]
    for key,value in evidence.count_sums.items():
        qualities = re.findall(r'q[13456]',key)
        if qualities and ''.join(qualities)==key:
            indices = [QUALITY_KEYS.index(q) for q in qualities]
            valid &= out_counts[:,indices].sum(axis=1)==value
        else:
            raise ModelInputError('unsupported_count_sum:' + key)
    for qi,key in enumerate(('white','green')):
        if key in evidence.split_counts:
            valid &= split_counts[:,qi] == evidence.split_counts[key]
        if key in evidence.split_quality_cells:
            valid &= split_cells[:,qi] == evidence.split_quality_cells[key]
        if key in evidence.split_avg_cells:
            valid &= np.abs(split_cells[:,qi]-split_counts[:,qi]*evidence.split_avg_cells[key]) < .01
    total_cells = out_cells.sum(axis=1)
    if evidence.total_grid_options:
        valid &= np.isin(total_cells,evidence.total_grid_options)
    elif evidence.total_grid_target is not None:
        valid &= np.abs(total_cells-evidence.total_grid_target)<.01
    if 'total' in evidence.avg_cells:
        valid &= np.abs(total_cells-out_n*evidence.avg_cells['total'])<=np.maximum(.01,out_n*.005)
    totals = out_values.sum(axis=1)
    valid &= totals >= max(evidence.public_category_total_value_floor,evidence.shape_average_subset_floor)
    for sample_n,value_floor in evidence.random_value_floors:
        valid &= (out_n >= sample_n) & (totals >= value_floor)
    if evidence.random_sample:
        sample_n=sum(evidence.random_sample.values())
        for index in np.flatnonzero(valid):
            likelihood=1.0
            for qi,key in enumerate(QUALITY_KEYS):
                observed_n=evidence.random_sample.get(key,0)
                if observed_n>out_counts[index,qi]:
                    likelihood=0.0
                    break
                likelihood*=math.comb(int(out_counts[index,qi]),observed_n)
            denominator=math.comb(int(out_n[index]),sample_n) if out_n[index]>=sample_n else 0
            if likelihood<=0 or denominator<=0:
                valid[index]=False
            else:
                logweight[index]+=math.log(likelihood/denominator)
    if not valid.any():
        raise ModelInputError('posterior_sampling_exhausted_for_constraints')
    selected = np.flatnonzero(valid)
    weight = np.exp(logweight[selected]-logweight[selected].max())
    weight /= weight.sum()
    def ranges(matrix):
        return {key:_weighted_range(matrix[selected,qi],weight) for qi,key in enumerate(QUALITY_KEYS)}
    display=_quality_display_posterior(out_counts[selected],out_cells[selected],totals[selected],weight,evidence,observed)
    return dict(**display,value_range=_weighted_range(totals[selected],weight),
                quality_count_ranges=ranges(out_counts),quality_cells_ranges=ranges(out_cells),
                red_value_range=_weighted_range(out_values[selected,4],weight),
                total_grid_range=_weighted_range(total_cells[selected],weight),
                antique_count_range=_weighted_range(out_a[selected],weight),
                red_antique_count_range=_weighted_range(out_r[selected],weight),
                total_count_range=_weighted_range(out_n[selected],weight),
                guarantee_violations=int(np.sum((out_a[selected]*2<=out_n[selected])|(out_r[selected]<2))),
                accepted_draws=len(selected),effective_sample_size=float(1/np.dot(weight,weight)),
                draws=draws,seed=seed,observed_physical_count=len(observed),
                count_prior=dict(COUNT_PRIOR,center=count_center,sigma=count_sigma,used=n_exact is None))
