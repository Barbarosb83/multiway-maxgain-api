"""oddType / outcome katalogları ve anlamsal eşlemenin doğrulanması."""

from __future__ import annotations

import pytest

from app.services.markets import MARKETS, UnknownOutcome, get_space, is_aggregate, mask_for
from app.services.odd_types import (
    KNOWN_UNMAPPABLE,
    ODD_TYPE_MARKET,
    ODD_TYPE_NAME,
    OUTCOME_BY_ODD_ID,
    OUTCOMES_BY_ODD_TYPE,
    REJECTED_MAPPINGS,
    resolve_odd_type,
    resolve_outcome,
)


def test_both_catalogs_are_loaded():
    pre = {key for key in ODD_TYPE_NAME if key[0] == 0}
    live = {key for key in ODD_TYPE_NAME if key[0] == 1}
    assert len(pre) == 559
    assert len(live) == 867
    assert len(OUTCOME_BY_ODD_ID) > 8000


def test_id_spaces_are_disjoint_between_pre_and_live():
    """Aynı sayı iki katalogda farklı piyasadır; bu yüzden isLive şart."""
    assert resolve_odd_type(24, is_live=1).name == "Double Chance (ALL)"
    assert not resolve_odd_type(24, is_live=0).in_catalog
    assert resolve_odd_type(1565, is_live=0).name == "3way"
    assert not resolve_odd_type(1565, is_live=1).in_catalog


def test_odd_id_resolves_to_odd_type_and_outcome():
    assert resolve_outcome(1571, is_live=0) == (1565, "1")
    assert resolve_outcome(2307, is_live=0) == (1481, "1X")
    assert resolve_outcome(80, is_live=1) == (24, "1X")
    assert resolve_outcome(999999, is_live=0) is None


def test_every_mapping_is_consistent_with_its_outcome_set():
    """Eşlenen her oddType'ın *bütün* outcome'ları piyasa tanımıyla çözümlenmeli.

    Bu, eşlemelerin gözle değil veriyle doğrulanmasıdır: yanlış bir eşleme
    çelişen seçimleri sessizce uyumlu gösterip max gain'i şişirebilir.
    """
    space = get_space("JOINT", 8)
    failures: list[str] = []

    for (is_live, odd_type_id), market_id in ODD_TYPE_MARKET.items():
        market = MARKETS[market_id]
        special = "0:1" if "HANDIKAP" in market_id else ("2.5" if market.needs_special else None)
        siblings = tuple(OUTCOMES_BY_ODD_TYPE.get((is_live, odd_type_id), ()))
        for outcome in siblings:
            try:
                mask_for(market_id, outcome, special, space.key, siblings)
            except UnknownOutcome as exc:
                failures.append(f"{'live' if is_live else 'pre'} {odd_type_id} {outcome!r}: {exc}")

    assert not failures, "Eşleme ile outcome kümesi uyuşmuyor:\n" + "\n".join(failures)


def test_rejected_mappings_are_exactly_the_documented_ones():
    """Beklenmeyen bir uyumsuzluk çıkarsa test kırılsın, sessizce düşmesin."""
    rejected = {(is_live, odd_type_id) for is_live, odd_type_id, _name, _why in REJECTED_MAPPINGS}
    assert rejected == set(KNOWN_UNMAPPABLE)


def test_aggregate_outcome_is_the_complement_of_its_siblings():
    """'Others' listelenen skorların hiçbiri demektir."""
    space = get_space("FLAT", 8)
    siblings = tuple(OUTCOMES_BY_ODD_TYPE[(0, 1456)])
    assert any(is_aggregate(o) for o in siblings)

    others = mask_for("DOGRU_SKOR", "Others", None, space.key, siblings)
    for sibling in siblings:
        if is_aggregate(sibling):
            continue
        assert others & mask_for("DOGRU_SKOR", sibling, None, space.key) == 0


@pytest.mark.parametrize(
    ("is_live", "odd_type_id", "expected_market"),
    [
        (0, 1565, "MS_1X2"),  # 3way
        (0, 1481, "CIFT_SANS"),  # Double Chance
        (0, 1500, "ALT_UST"),  # Over/Under
        (0, 1519, "GOL_SAYISI"),  # Total Goals -> aralık, Over/Under değil
        (0, 1628, "IY_GOL_SAYISI"),  # 1st Half - Total Goals -> 0/1/2+
        (0, 1487, "GOL_SAYISI_EV"),  # Goals Home -> 0/1/2/3+
        (0, 1762, "ALT_UST_3WAY"),  # Total 3way -> Over/X/Under
        (1, 24, "CIFT_SANS"),
        (1, 178, "IY_MS"),  # HH/HD/HA kodlaması
        (1, 750, "IY_MS"),  # 11/1X/21 kodlaması
    ],
)
def test_known_odd_types_map_to_expected_markets(is_live, odd_type_id, expected_market):
    assert ODD_TYPE_MARKET[(is_live, odd_type_id)] == expected_market
