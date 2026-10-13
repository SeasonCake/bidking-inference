"""First-pass Monte Carlo for a single BidMap session.

Drop pools in BidKing are **multi-level**: a map's top pool typically
points to a "quality distribution" pool, which points to a "category
blind box", which finally points to a category-quality leaf pool of
actual Item.txt items. ``DropEntry.category == 9999`` marks an entry
as a pool-reference (recurse with ``item_id`` as the next pool id);
any other category means the entry is a leaf with a real ``item_id``.

Strategy:

1.  For each map, flatten the multi-level pool tree into a single
    distribution of ``leaf_item_id → effective_probability``. This is
    done once per simulation call (and once per sub-pool for anthology
    maps).

2.  For each trial:
    - Pick an effective leaf-distribution (anthology maps choose a
      sub-pool weighted by ``sub_pool_weights``; leaf maps always use
      their own).
    - Sample ``K`` items from that distribution where
      ``K ~ Uniform[items_per_session_min, items_per_session_max]``.
    - Count per item ``~ Uniform[n_min, n_max]`` from the leaf entry.
    - Total value = ``sum(value × count)``.

Known v1 simplifications (deliberate, documented):

- **Sampling is with replacement.** Real auctions show unique slots,
  so the variance we report is wider than the truth. For ranking maps
  by expected value the bias is tiny; for tight quantile claims it
  matters and is a Q1-followup.
- **No hero skills.** Hero effects (e.g. 艾哈迈德 revealing counts)
  change player *decisions*, not the underlying loot distribution.
  Project_vision Q4 will model their marginal value separately.
- **No bidding / budget.** We assume the player buys everything that
  drops; result is "gross take if you won everything".
- **No grid placement.** Q3 needs item footprints, deferred.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Mapping

import numpy as np
from pydantic import BaseModel

from bidking_lab.extract.bid_map_table import BidMap
from bidking_lab.extract.drop_table import DropPool
from bidking_lab.extract.item_table import Item

POOL_REFERENCE_CATEGORY = 9999
"""Sentinel in ``DropEntry.category``: this entry refers to another pool
rather than to a real Item.txt item."""

_MAX_POOL_DEPTH = 16


def is_physical_loot_item(item: Item) -> bool:
    """Return True for auctionable collectibles that can affect inference."""
    return item.quality > 0 and item.shape_w > 0 and item.shape_h > 0 and item.value > 0


class FlattenedPool(BaseModel):
    """Flat (item_id, prob, count-range, value) view of a multi-level pool.

    All arrays have the same length ``N``. ``probabilities`` sums to 1.0.
    """

    item_ids: list[int]
    probabilities: list[float]
    n_min: list[int]
    n_max: list[int]
    values: list[int]
    resolution_status: str = "complete"
    unresolved_probability: float = 0.0
    unresolved_leaf_count: int = 0
    resolution_policy: str = ""
    resolution_generation: str = ""


@dataclass(frozen=True)
class DropPoolResolutionPolicy:
    """Generation-bound authority for one explicitly known legacy leaf gap.

    Direct callers remain strict by default.  Product runtime may bind this
    policy only after the exact BidMap/Item/Drop and derived map-authority
    inputs have been verified.  The surviving distribution is conditional on
    known physical support and the omitted probability mass stays observable
    on :class:`FlattenedPool`.
    """

    policy_id: str
    package_generation: str
    allowed_root_pool_ids: frozenset[int]
    allowed_unknown_leaves: frozenset[tuple[int, int]]


class _VerifiedPolicyBindingMarker:
    """Opaque capability retained across safe wrapper copies.

    This is a Python API boundary, not a sandbox against arbitrary code already
    executing in this module's process. Public constructors can build an
    ordinary read-only mapping, but only exact package verification receives
    this private marker and may authorize an incomplete legacy leaf.
    """

    def __copy__(self) -> _VerifiedPolicyBindingMarker:
        return self

    def __deepcopy__(
        self,
        memo: dict[int, object],
    ) -> _VerifiedPolicyBindingMarker:
        del memo
        return self


_VERIFIED_POLICY_BINDING = _VerifiedPolicyBindingMarker()


def _strict_resolution_policy_shape(policy: object) -> bool:
    if type(policy) is not DropPoolResolutionPolicy:  # noqa: E721
        return False
    values = vars(policy)
    if set(values) != {
        "policy_id",
        "package_generation",
        "allowed_root_pool_ids",
        "allowed_unknown_leaves",
    }:
        return False
    policy_id = values["policy_id"]
    package_generation = values["package_generation"]
    allowed_root_pool_ids = values["allowed_root_pool_ids"]
    allowed_unknown_leaves = values["allowed_unknown_leaves"]
    if (
        type(policy_id) is not str  # noqa: E721
        or not policy_id
        or policy_id != policy_id.strip()
        or type(package_generation) is not str  # noqa: E721
        or not package_generation
        or package_generation != package_generation.strip()
        or type(allowed_root_pool_ids) is not frozenset  # noqa: E721
        or not allowed_root_pool_ids
        or any(
            type(pool_id) is not int or pool_id <= 0  # noqa: E721
            for pool_id in allowed_root_pool_ids
        )
        or type(allowed_unknown_leaves) is not frozenset  # noqa: E721
        or not allowed_unknown_leaves
    ):
        return False
    return all(
        type(leaf) is tuple  # noqa: E721
        and len(leaf) == 2
        and type(leaf[0]) is int  # noqa: E721
        and type(leaf[1]) is int  # noqa: E721
        and leaf[0] > 0
        and leaf[1] > 0
        for leaf in allowed_unknown_leaves
    )


class PolicyBoundDropPools(Mapping[int, DropPool]):
    """Read-only Drop mapping that may carry a verified resolution policy.

    The public constructor, including through a subclass, creates an
    unverified wrapper. It remains usable as a mapping but cannot authorize an
    unknown leaf. Product verification uses the private factory below.
    """

    def __init__(
        self,
        pools: Mapping[int, DropPool],
        policy: DropPoolResolutionPolicy,
        *,
        _verification_marker: object | None = None,
    ) -> None:
        self._pools = pools
        self._resolution_policy = policy
        self._verification_marker = (
            _VERIFIED_POLICY_BINDING
            if _verification_marker is _VERIFIED_POLICY_BINDING
            and _strict_resolution_policy_shape(policy)
            else None
        )

    def __getitem__(self, key: int) -> DropPool:
        return self._pools[key]

    def __iter__(self) -> Iterator[int]:
        return iter(self._pools)

    def __len__(self) -> int:
        return len(self._pools)

    @property
    def resolution_policy(self) -> DropPoolResolutionPolicy:
        return self._resolution_policy


def _verified_resolution_policy(
    drops: object,
    *,
    expected_generation: str | None = None,
) -> DropPoolResolutionPolicy | None:
    if (
        type(drops) is not PolicyBoundDropPools  # noqa: E721
        or drops._verification_marker is not _VERIFIED_POLICY_BINDING
        or not _strict_resolution_policy_shape(drops.resolution_policy)
    ):
        return None
    policy = drops.resolution_policy
    if (
        expected_generation is not None
        and (
            type(expected_generation) is not str  # noqa: E721
            or policy.package_generation != expected_generation
        )
    ):
        return None
    return policy


def _bind_verified_drop_pool_resolution_policy(
    drops: Mapping[int, DropPool],
    policy: DropPoolResolutionPolicy,
) -> PolicyBoundDropPools:
    """Internal capability factory used only after package verification."""

    if not _strict_resolution_policy_shape(policy):
        raise ValueError("drop resolution policy shape is invalid")
    return PolicyBoundDropPools(
        drops,
        policy,
        _verification_marker=_VERIFIED_POLICY_BINDING,
    )


def _promote_verified_drop_pool_policy_generation(
    drops: Mapping[int, DropPool],
    *,
    source_package_generation: str,
    package_generation: str,
) -> Mapping[int, DropPool]:
    """Promote one verified binding after a second package gate succeeds."""

    if (
        type(source_package_generation) is not str  # noqa: E721
        or type(package_generation) is not str  # noqa: E721
        or not source_package_generation
        or not package_generation
    ):
        return drops
    policy = _verified_resolution_policy(
        drops,
        expected_generation=source_package_generation,
    )
    if policy is None:
        return drops
    promoted = DropPoolResolutionPolicy(
        policy_id=policy.policy_id,
        package_generation=package_generation,
        allowed_root_pool_ids=policy.allowed_root_pool_ids,
        allowed_unknown_leaves=policy.allowed_unknown_leaves,
    )
    return _bind_verified_drop_pool_resolution_policy(drops._pools, promoted)


class RankMapAlgorithmPriorBoundDropPools(Mapping[int, DropPool]):
    """Exact current pool ids backed by a generation-bound algorithm prior.

    The bound values are already flattened model distributions.  They are not
    asserted to be rows from Drop/DropMap, and the underlying Drop mapping is
    left unchanged for every pool outside the explicit current RankMap scope.
    A verified legacy policy is retained only when its promoted generation is
    exactly the same as this RankMap package generation; explicit modeled pools
    always return before legacy traversal begins.
    """

    def __init__(
        self,
        pools: Mapping[int, DropPool],
        flattened_by_pool_id: Mapping[int, FlattenedPool],
        *,
        package_generation: str,
        authority_mode: str,
        flattened_by_map_id: Mapping[int, tuple[int, FlattenedPool]] | None = None,
    ) -> None:
        self._pools = pools
        self._flattened_by_pool_id = {
            pool_id: value.model_copy(deep=True)
            for pool_id, value in flattened_by_pool_id.items()
        }
        self._flattened_by_map_id: dict[int, tuple[int, FlattenedPool]] = {}
        for map_id, scoped in (flattened_by_map_id or {}).items():
            if (type(map_id) is not int or map_id <= 0
                    or type(scoped) is not tuple or len(scoped) != 2):
                raise ValueError("invalid map-conditioned prior scope")
            pool_id, value = scoped
            if (type(pool_id) is not int or pool_id <= 0
                    or type(value) is not FlattenedPool
                    or value.resolution_generation != package_generation
                    or value.resolution_policy != authority_mode):
                raise ValueError("invalid map-conditioned prior identity")
            self._flattened_by_map_id[map_id] = (pool_id, value.model_copy(deep=True))
        self._map_pool_ids = frozenset(pool_id for pool_id, _ in self._flattened_by_map_id.values())
        self._unscoped_models = dict(self._flattened_by_pool_id)
        self._ambiguous_pool_ids: set[int] = set()
        for pool_id, value in self._flattened_by_map_id.values():
            previous = self._unscoped_models.get(pool_id)
            if previous is None:
                self._unscoped_models[pool_id] = value
            elif previous != value:
                self._ambiguous_pool_ids.add(pool_id)
        self.package_generation = package_generation
        self.authority_mode = authority_mode
        self._resolution_policy = _verified_resolution_policy(
            pools,
            expected_generation=package_generation,
        )
        self._resolution_policy_marker = (
            _VERIFIED_POLICY_BINDING
            if self._resolution_policy is not None
            else None
        )

    def __getitem__(self, key: int) -> DropPool:
        return self._pools[key]

    def __iter__(self) -> Iterator[int]:
        return iter(self._pools)

    def __len__(self) -> int:
        return len(self._pools)

    @property
    def resolution_policy(self) -> DropPoolResolutionPolicy | None:
        return self._resolution_policy

    @property
    def bound_pool_ids(self) -> frozenset[int]:
        return frozenset(self._unscoped_models)

    @property
    def bound_map_ids(self) -> frozenset[int]:
        return frozenset(self._flattened_by_map_id)

    def flattened_pool(self, pool_id: int, *, map_id: int | None = None) -> FlattenedPool | None:
        if self._flattened_by_map_id and map_id is not None:
            if type(map_id) is not int or map_id <= 0:
                raise DropPoolResolutionError("map_context_unknown", pool_id=pool_id)
            scoped = self._flattened_by_map_id.get(map_id)
            if scoped is not None:
                if type(pool_id) is not int or scoped[0] != pool_id:
                    raise DropPoolResolutionError("map_pool_mismatch", pool_id=pool_id)
                return scoped[1].model_copy(deep=True)
            if pool_id in self._map_pool_ids:
                raise DropPoolResolutionError("map_context_unknown", pool_id=pool_id)
        if pool_id in self._ambiguous_pool_ids:
            raise DropPoolResolutionError("map_context_required", pool_id=pool_id)
        value = self._unscoped_models.get(pool_id)
        return value.model_copy(deep=True) if value is not None else None


def bind_drop_pool_resolution_policy(
    drops: Mapping[int, DropPool],
    policy: DropPoolResolutionPolicy,
) -> PolicyBoundDropPools:
    """Return an unverified, fail-closed public policy wrapper.

    Caller-provided policy data is not evidence that package inputs were
    verified. Product code uses an internal authority-bound factory.
    """

    return PolicyBoundDropPools(drops, policy)


def bind_rankmap_algorithm_prior(
    drops: Mapping[int, DropPool],
    flattened_by_pool_id: Mapping[int, FlattenedPool],
    *,
    package_generation: str,
    authority_mode: str,
    flattened_by_map_id: Mapping[int, tuple[int, FlattenedPool]] | None = None,
) -> RankMapAlgorithmPriorBoundDropPools:
    """Bind modeled priors, retaining physical-map scope at shared pool ids."""

    return RankMapAlgorithmPriorBoundDropPools(
        drops,
        flattened_by_pool_id,
        package_generation=package_generation,
        authority_mode=authority_mode,
        flattened_by_map_id=flattened_by_map_id,
    )


class DropPoolResolutionError(ValueError):
    """A reachable Drop tree cannot be resolved to a complete item closure."""

    def __init__(
        self,
        reason: str,
        *,
        pool_id: int,
        item_id: int | None = None,
    ) -> None:
        self.reason = reason
        self.pool_id = pool_id
        self.item_id = item_id
        detail = f"{reason}: pool_id={pool_id}"
        if item_id is not None:
            detail += f", item_id={item_id}"
        super().__init__(f"drop pool resolution incomplete: {detail}")


class SimulationResult(BaseModel):
    """Summary statistics of an N-trial Monte Carlo run."""

    map_id: int
    map_name: str
    n_trials: int
    pool_size_after_flatten: int
    mean: float
    std: float
    min: int
    q05: int
    q50: int
    q95: int
    max: int


def flatten_pool(
    pool_id: int,
    drops: Mapping[int, DropPool],
    items: Mapping[int, Item],
    *,
    map_id: int | None = None,
) -> FlattenedPool:
    """Walk ``pool_id`` recursively, expanding cat-9999 references, until
    every node is a leaf entry with a known Item.txt item.

    Returns a single distribution over leaf items with their effective
    probabilities (path probability product) and count ranges. Same
    ``item_id`` reached via multiple paths gets its probabilities
    summed and count range unioned.
    """
    if type(drops) is RankMapAlgorithmPriorBoundDropPools:  # noqa: E721
        modeled = drops.flattened_pool(pool_id, map_id=map_id)
        if modeled is not None:
            return modeled

    bucket: dict[int, dict[str, float | int]] = {}
    if type(drops) is RankMapAlgorithmPriorBoundDropPools:  # noqa: E721
        policy = (
            drops.resolution_policy
            if drops._resolution_policy_marker is _VERIFIED_POLICY_BINDING
            and _strict_resolution_policy_shape(drops.resolution_policy)
            and drops.resolution_policy.package_generation
            == drops.package_generation
            else None
        )
    else:
        policy = _verified_resolution_policy(drops)
    unresolved_probability = 0.0
    unresolved_leaves: set[tuple[int, int]] = set()
    first_unresolved: tuple[int, int] | None = None

    def walk(pid: int, path_prob: float, depth: int) -> None:
        nonlocal first_unresolved, unresolved_probability
        if depth > _MAX_POOL_DEPTH:
            raise RuntimeError(
                f"pool resolution exceeded depth {_MAX_POOL_DEPTH} at pool_id={pid}"
            )
        pool = drops.get(pid)
        if pool is None:
            raise DropPoolResolutionError("missing_pool", pool_id=pid)
        if not pool.entries:
            raise DropPoolResolutionError("empty_pool", pool_id=pid)
        total = sum(e.weight for e in pool.entries)
        if total <= 0:
            raise DropPoolResolutionError("empty_pool", pool_id=pid)
        for entry in pool.entries:
            if entry.weight <= 0:
                continue
            edge_p = path_prob * (entry.weight / total)
            if entry.category == POOL_REFERENCE_CATEGORY:
                walk(entry.item_id, edge_p, depth + 1)
                continue
            # Leaf entry: must resolve to a real physical collectible to count.
            item = items.get(entry.item_id)
            if item is None:
                leaf_identity = (pid, entry.item_id)
                if (
                    policy is not None
                    and pool_id in policy.allowed_root_pool_ids
                    and leaf_identity in policy.allowed_unknown_leaves
                ):
                    unresolved_probability += edge_p
                    unresolved_leaves.add(leaf_identity)
                    if first_unresolved is None:
                        first_unresolved = leaf_identity
                    continue
                raise DropPoolResolutionError(
                    "unknown_item",
                    pool_id=pid,
                    item_id=entry.item_id,
                )
            if not is_physical_loot_item(item):
                continue
            slot = bucket.get(entry.item_id)
            if slot is None:
                bucket[entry.item_id] = {
                    "p": edge_p,
                    "n_min": entry.n_min,
                    "n_max": entry.n_max,
                }
            else:
                slot["p"] = float(slot["p"]) + edge_p
                slot["n_min"] = min(int(slot["n_min"]), entry.n_min)
                slot["n_max"] = max(int(slot["n_max"]), entry.n_max)

    walk(pool_id, 1.0, 0)

    item_ids = sorted(bucket.keys())
    probs = [float(bucket[i]["p"]) for i in item_ids]
    n_min = [int(bucket[i]["n_min"]) for i in item_ids]
    n_max = [int(bucket[i]["n_max"]) for i in item_ids]
    values = [int(items[i].value) for i in item_ids]
    total_p = sum(probs)
    if unresolved_leaves and total_p <= 0:
        assert first_unresolved is not None
        raise DropPoolResolutionError(
            "unknown_item",
            pool_id=first_unresolved[0],
            item_id=first_unresolved[1],
        )
    if total_p > 0:
        probs = [p / total_p for p in probs]
    return FlattenedPool(
        item_ids=item_ids,
        probabilities=probs,
        n_min=n_min,
        n_max=n_max,
        values=values,
        resolution_status=(
            "conditional_known_support" if unresolved_leaves else "complete"
        ),
        unresolved_probability=float(unresolved_probability),
        unresolved_leaf_count=len(unresolved_leaves),
        resolution_policy=policy.policy_id if unresolved_leaves and policy else "",
        resolution_generation=(
            policy.package_generation if unresolved_leaves and policy else ""
        ),
    )


def _flattened_arrays(
    fp: FlattenedPool,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    return (
        np.array(fp.item_ids, dtype=np.int64),
        np.array(fp.probabilities, dtype=np.float64),
        np.array(fp.n_min, dtype=np.int64),
        np.array(fp.n_max, dtype=np.int64),
        np.array(fp.values, dtype=np.int64),
    )


def simulate_map(
    map_id: int,
    *,
    maps: Mapping[int, BidMap],
    drops: Mapping[int, DropPool],
    items: Mapping[int, Item],
    n_trials: int = 10_000,
    rng: np.random.Generator | None = None,
) -> SimulationResult:
    """Run ``n_trials`` Monte Carlo sessions of the given map.

    See module docstring for the model. Returns a ``SimulationResult``
    with mean / std / quantile summary of session total value.
    """
    rng = rng or np.random.default_rng()
    bid_map = maps[map_id]

    k_lo = bid_map.items_per_session_min
    k_hi = bid_map.items_per_session_max
    if k_hi < k_lo:
        raise ValueError(f"map {map_id} has inverted item range [{k_lo},{k_hi}]")

    # Build the effective leaf distribution(s). For leaf maps it's one.
    # For anthology maps we keep one per sub-pool plus the anthology
    # weights so we can pick a sub-pool per trial.
    if not bid_map.sub_pool_weights:
        flat = flatten_pool(bid_map.drop_pool_id, drops, items, map_id=bid_map.map_id)
        sub_pool_arrays = [_flattened_arrays(flat)]
        sub_pool_p = np.array([1.0])
    else:
        arrays_list = []
        weight_list = []
        for sub_map_id, sub_weight in bid_map.sub_pool_weights:
            sub_map = maps.get(sub_map_id)
            if sub_map is None:
                continue
            flat = flatten_pool(sub_map.drop_pool_id, drops, items, map_id=sub_map.map_id)
            if not flat.item_ids:
                continue
            arrays_list.append(_flattened_arrays(flat))
            weight_list.append(sub_weight)
        if not arrays_list:
            arrays_list = [_flattened_arrays(FlattenedPool(item_ids=[], probabilities=[], n_min=[], n_max=[], values=[]))]
            weight_list = [1.0]
        sub_pool_arrays = arrays_list
        sub_pool_p = np.array(weight_list, dtype=np.float64)
        sub_pool_p = sub_pool_p / sub_pool_p.sum()

    pool_size_after_flatten = max(len(a[0]) for a in sub_pool_arrays)

    results = np.zeros(n_trials, dtype=np.float64)
    for t in range(n_trials):
        sub_idx = (
            int(rng.choice(len(sub_pool_arrays), p=sub_pool_p))
            if len(sub_pool_arrays) > 1
            else 0
        )
        item_ids, p, n_mins, n_maxs, values = sub_pool_arrays[sub_idx]
        pool_size = len(item_ids)
        if pool_size == 0:
            results[t] = 0.0
            continue
        k = int(rng.integers(k_lo, k_hi + 1))
        # Sampling with replacement — see module docstring for caveat.
        idx = rng.choice(pool_size, size=k, replace=True, p=p)
        counts = rng.integers(n_mins[idx], n_maxs[idx] + 1)
        results[t] = float((values[idx] * counts).sum())

    return SimulationResult(
        map_id=map_id,
        map_name=bid_map.name,
        n_trials=n_trials,
        pool_size_after_flatten=pool_size_after_flatten,
        mean=float(results.mean()),
        std=float(results.std()),
        min=int(results.min()),
        q05=int(np.percentile(results, 5)),
        q50=int(np.percentile(results, 50)),
        q95=int(np.percentile(results, 95)),
        max=int(results.max()),
    )


__all__ = (
    "DropPoolResolutionError",
    "DropPoolResolutionPolicy",
    "FlattenedPool",
    "PolicyBoundDropPools",
    "RankMapAlgorithmPriorBoundDropPools",
    "SimulationResult",
    "bind_drop_pool_resolution_policy",
    "bind_rankmap_algorithm_prior",
    "flatten_pool",
    "is_physical_loot_item",
    "simulate_map",
)
