from __future__ import annotations

import copy
from collections.abc import Iterator, Mapping
from types import SimpleNamespace

import pytest

from bidking_lab.extract.current_rankmap_package_contract import (
    CURRENT_PACKAGE_GENERATION,
    CURRENT_RANKMAP_MAP_IDS,
)
from bidking_lab.extract.drop_table import DropEntry, DropPool
from bidking_lab.extract.item_table import Item
from bidking_lab.live import monitor as live_monitor
from bidking_lab.live.monitor import MonitorTables
from bidking_lab.simulation.basic_mc import (
    DropPoolResolutionError,
    DropPoolResolutionPolicy,
    FlattenedPool,
    PolicyBoundDropPools,
    RankMapAlgorithmPriorBoundDropPools,
    _bind_verified_drop_pool_resolution_policy,
    _promote_verified_drop_pool_policy_generation,
    bind_drop_pool_resolution_policy,
    bind_rankmap_algorithm_prior,
    flatten_pool,
)


LEGACY_GENERATION = "verified-update606-generation"
RANKMAP_MODE = "rankmap_drop_model_algorithm_prior"


def _item(item_id: int, *, value: int = 100) -> Item:
    return Item(
        item_id=item_id,
        name=f"item_{item_id}",
        description="",
        name_key=f"key_{item_id}",
        desc_key="",
        quality=3,
        quality_color="blue",
        value=value,
        shape_w=1,
        shape_h=1,
        tags=[],
        allowed_shelves=[],
        icon_name="",
        model_name="",
        raw_row=["0"] * 41,
    )


def _pool(pool_id: int, entries: list[tuple[int, int, int]]) -> DropPool:
    return DropPool(
        pool_id=pool_id,
        name=f"pool_{pool_id}",
        description="",
        pool_type=2,
        entries=[
            DropEntry(
                category=category,
                item_id=item_id,
                n_min=1,
                n_max=1,
                weight=weight,
            )
            for category, item_id, weight in entries
        ],
    )


def _legacy_drops() -> dict[int, DropPool]:
    return {
        900: _pool(900, [(9999, 1066, 1)]),
        901: _pool(901, [(9999, 1066, 1)]),
        902: _pool(902, [(9999, 1067, 1)]),
        1066: _pool(1066, [(101, 1, 3), (106, 1_106_013, 1)]),
        1067: _pool(1067, [(101, 1, 3), (106, 1_106_014, 1)]),
    }


def _policy(
    *,
    generation: str = LEGACY_GENERATION,
) -> DropPoolResolutionPolicy:
    return DropPoolResolutionPolicy(
        policy_id="verified-update606-legacy-gap",
        package_generation=generation,
        allowed_root_pool_ids=frozenset({900}),
        allowed_unknown_leaves=frozenset({(1066, 1_106_013)}),
    )


def _modeled() -> FlattenedPool:
    return FlattenedPool(
        item_ids=[2],
        probabilities=[1.0],
        n_min=[1],
        n_max=[1],
        values=[250],
        resolution_status="complete",
        resolution_policy=RANKMAP_MODE,
        resolution_generation=CURRENT_PACKAGE_GENERATION,
    )


def _verified_legacy() -> PolicyBoundDropPools:
    return _bind_verified_drop_pool_resolution_policy(
        _legacy_drops(),
        _policy(),
    )


def _composed() -> RankMapAlgorithmPriorBoundDropPools:
    promoted = _promote_verified_drop_pool_policy_generation(
        _verified_legacy(),
        source_package_generation=LEGACY_GENERATION,
        package_generation=CURRENT_PACKAGE_GENERATION,
    )
    return bind_rankmap_algorithm_prior(
        promoted,
        {2706: _modeled()},
        package_generation=CURRENT_PACKAGE_GENERATION,
        authority_mode=RANKMAP_MODE,
    )


def _assert_legacy_unknown_fails(drops: Mapping[int, DropPool]) -> None:
    with pytest.raises(DropPoolResolutionError, match="unknown_item"):
        flatten_pool(900, drops, {1: _item(1), 2: _item(2)})


def test_verified_policy_is_promoted_only_after_matching_source_generation() -> None:
    promoted = _promote_verified_drop_pool_policy_generation(
        _verified_legacy(),
        source_package_generation=LEGACY_GENERATION,
        package_generation=CURRENT_PACKAGE_GENERATION,
    )
    composed = bind_rankmap_algorithm_prior(
        promoted,
        {2706: _modeled()},
        package_generation=CURRENT_PACKAGE_GENERATION,
        authority_mode=RANKMAP_MODE,
    )

    assert type(promoted) is PolicyBoundDropPools  # noqa: E721
    assert composed.resolution_policy is not None
    assert composed.resolution_policy.package_generation == CURRENT_PACKAGE_GENERATION
    flattened = flatten_pool(900, composed, {1: _item(1), 2: _item(2)})
    assert flattened.resolution_status == "conditional_known_support"
    assert flattened.resolution_policy == "verified-update606-legacy-gap"
    assert flattened.resolution_generation == CURRENT_PACKAGE_GENERATION
    assert flattened.unresolved_probability == pytest.approx(0.25)


def test_rankmap_modeled_pool_wins_without_applying_legacy_policy() -> None:
    drops = _composed()

    first = flatten_pool(2706, drops, {2: _item(2, value=250)})
    first.item_ids.append(999)
    second = flatten_pool(2706, drops, {2: _item(2, value=250)})

    assert second == _modeled()
    assert second.resolution_status == "complete"
    assert second.unresolved_probability == 0.0


def test_composed_monitor_receipt_keeps_promoted_generation() -> None:
    tables = MonitorTables(
        maps={
            2501: SimpleNamespace(map_id=2501, sub_pool_weights=[], drop_pool_id=900),
            5606: SimpleNamespace(map_id=5606, sub_pool_weights=[], drop_pool_id=2706),
        },
        drops=_composed(),
        items={1: _item(1), 2: _item(2, value=250)},
    )

    receipt = live_monitor._drop_pool_support_receipt(2501, tables)
    assert receipt["status"] == "legacy_incomplete_conditional_known_support"
    assert receipt["package_generation"] == CURRENT_PACKAGE_GENERATION
    assert receipt["unresolved_probability_ppm"] == 250_000
    assert live_monitor._drop_pool_support_receipt(5606, tables) == {}


@pytest.mark.parametrize("pool_id", [901, 902])
def test_composed_policy_keeps_unauthorized_root_or_leaf_fail_closed(
    pool_id: int,
) -> None:
    with pytest.raises(DropPoolResolutionError, match="unknown_item"):
        flatten_pool(pool_id, _composed(), {1: _item(1), 2: _item(2)})


def test_public_exact_constructor_and_binder_cannot_claim_verification() -> None:
    direct = PolicyBoundDropPools(
        _legacy_drops(),
        _policy(generation=CURRENT_PACKAGE_GENERATION),
    )
    public_bound = bind_drop_pool_resolution_policy(
        _legacy_drops(),
        _policy(generation=CURRENT_PACKAGE_GENERATION),
    )

    for value in (direct, public_bound):
        composed = bind_rankmap_algorithm_prior(
            value,
            {2706: _modeled()},
            package_generation=CURRENT_PACKAGE_GENERATION,
            authority_mode=RANKMAP_MODE,
        )
        assert composed.resolution_policy is None
        _assert_legacy_unknown_fails(composed)
        with pytest.raises(AttributeError):
            value.resolution_policy = _policy(  # type: ignore[misc]
                generation=CURRENT_PACKAGE_GENERATION,
            )


def test_subclass_proxy_plain_and_fake_attribute_cannot_claim_verification() -> None:
    class PolicySubclass(PolicyBoundDropPools):
        pass

    class PolicyProxy(Mapping[int, DropPool]):
        resolution_policy = _policy(generation=CURRENT_PACKAGE_GENERATION)

        def __init__(self) -> None:
            self._pools = _legacy_drops()

        def __getitem__(self, key: int) -> DropPool:
            return self._pools[key]

        def __iter__(self) -> Iterator[int]:
            return iter(self._pools)

        def __len__(self) -> int:
            return len(self._pools)

    values: tuple[Mapping[int, DropPool], ...] = (
        PolicySubclass(
            _legacy_drops(),
            _policy(generation=CURRENT_PACKAGE_GENERATION),
        ),
        PolicyProxy(),
        _legacy_drops(),
    )
    for value in values:
        composed = bind_rankmap_algorithm_prior(
            value,
            {2706: _modeled()},
            package_generation=CURRENT_PACKAGE_GENERATION,
            authority_mode=RANKMAP_MODE,
        )
        assert composed.resolution_policy is None
        _assert_legacy_unknown_fails(composed)


def test_wrong_generation_is_not_composed_without_verified_promotion() -> None:
    for generation in (LEGACY_GENERATION, "wrong-generation"):
        verified = _bind_verified_drop_pool_resolution_policy(
            _legacy_drops(),
            _policy(generation=generation),
        )
        composed = bind_rankmap_algorithm_prior(
            verified,
            {2706: _modeled()},
            package_generation=CURRENT_PACKAGE_GENERATION,
            authority_mode=RANKMAP_MODE,
        )
        assert composed.resolution_policy is None
        _assert_legacy_unknown_fails(composed)


@pytest.mark.parametrize("generation", [None, 1, True, "", " current "])
def test_internal_factory_rejects_missing_or_noncanonical_generation(
    generation: object,
) -> None:
    malformed = object.__new__(DropPoolResolutionPolicy)
    object.__setattr__(malformed, "policy_id", "malformed")
    if generation is not None:
        object.__setattr__(malformed, "package_generation", generation)
    object.__setattr__(malformed, "allowed_root_pool_ids", frozenset({900}))
    object.__setattr__(
        malformed,
        "allowed_unknown_leaves",
        frozenset({(1066, 1_106_013)}),
    )

    with pytest.raises(ValueError, match="policy shape"):
        _bind_verified_drop_pool_resolution_policy(_legacy_drops(), malformed)


def test_verified_wrapper_copy_deepcopy_and_read_only_semantics() -> None:
    verified_legacy = _verified_legacy()
    for cloned in (copy.copy(verified_legacy), copy.deepcopy(verified_legacy)):
        assert type(cloned) is PolicyBoundDropPools  # noqa: E721
        promoted = _promote_verified_drop_pool_policy_generation(
            cloned,
            source_package_generation=LEGACY_GENERATION,
            package_generation=CURRENT_PACKAGE_GENERATION,
        )
        assert type(promoted) is PolicyBoundDropPools  # noqa: E721
        assert promoted.resolution_policy.package_generation == (
            CURRENT_PACKAGE_GENERATION
        )
    with pytest.raises(AttributeError):
        verified_legacy.resolution_policy = _policy(  # type: ignore[misc]
            generation=CURRENT_PACKAGE_GENERATION,
        )

    drops = _composed()

    for cloned in (copy.copy(drops), copy.deepcopy(drops)):
        assert cloned.resolution_policy is not None
        assert cloned.resolution_policy.package_generation == CURRENT_PACKAGE_GENERATION
        assert flatten_pool(900, cloned, {1: _item(1)}).resolution_status == (
            "conditional_known_support"
        )
    with pytest.raises(TypeError):
        drops[900] = _pool(900, [])  # type: ignore[index]
    with pytest.raises(AttributeError):
        drops.bound_pool_ids.add(2707)  # type: ignore[attr-defined]
    with pytest.raises(AttributeError):
        drops.resolution_policy = _policy(  # type: ignore[misc]
            generation=CURRENT_PACKAGE_GENERATION,
        )


def test_current_rankmap_scope_is_exact_18_and_keeps_chenmei_isolated() -> None:
    assert len(CURRENT_RANKMAP_MAP_IDS) == 18
    assert {2101, 2107, 5601, 5606, 5607}.issubset(CURRENT_RANKMAP_MAP_IDS)
    assert 5506 not in CURRENT_RANKMAP_MAP_IDS
