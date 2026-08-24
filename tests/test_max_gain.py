"""Hesaplama motoru testleri."""

from __future__ import annotations

import itertools
import random
from decimal import ROUND_DOWN, Decimal

import pytest

from app.services.max_gain import (
    CouponError,
    CouponInput,
    EventInput,
    SelectionInput,
    calculate_max_gain,
    elementary_symmetric,
)
from tests.reference import brute_force_max_gain


def sel(sid: str, odds: str) -> SelectionInput:
    return SelectionInput(id=sid, odds=Decimal(odds))


def ev(eid: str, *odds: str, banker: bool = False) -> EventInput:
    selections = tuple(sel(f"o{i}", o) for i, o in enumerate(odds))
    return EventInput(id=eid, selections=selections, banker=banker)


def coupon(*events: EventInput, stake: str = "100.00", **kwargs) -> CouponInput:
    return CouponInput(events=events, stake=Decimal(stake), **kwargs)


# --------------------------------------------------------------------------- #
# Elementer simetrik polinom
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "values",
    [
        [Decimal("2")],
        [Decimal("1.5"), Decimal("2.5")],
        [Decimal("1.2"), Decimal("3.4"), Decimal("2.0"), Decimal("5.5")],
    ],
)
def test_elementary_symmetric_matches_explicit_combinations(values):
    e = elementary_symmetric(values)
    assert e[0] == Decimal(1)
    for k in range(1, len(values) + 1):
        expected = sum(
            (
                # her k'lı alt kümenin çarpımı
                __import__("math").prod(subset, start=Decimal(1))
                for subset in itertools.combinations(values, k)
            ),
            Decimal(0),
        )
        assert e[k] == expected, f"e[{k}] uyuşmadı"


# --------------------------------------------------------------------------- #
# Elle hesaplanabilir temel senaryolar
# --------------------------------------------------------------------------- #


def test_plain_parlay_is_product_of_odds():
    """Tek seçimli 3 maçlık kombine: max gain = stake x oranların çarpımı."""
    result = calculate_max_gain(coupon(ev("m1", "2.00"), ev("m2", "3.00"), ev("m3", "1.50")))
    assert result.line_count == 1
    assert result.stake_per_line == Decimal("100.00")
    assert result.max_gain == Decimal("900.00")  # 100 * 2 * 3 * 1.5
    assert result.net_profit == Decimal("800.00")


def test_multiway_splits_stake_and_picks_best_odds():
    """m1'de iki seçim -> 2 satır; en iyi senaryoda yüksek oranlı seçim tutar."""
    result = calculate_max_gain(coupon(ev("m1", "2.00", "4.00"), ev("m2", "3.00")))
    assert result.line_count == 2
    assert result.stake_per_line == Decimal("50.00")
    # 50 * 4.00 * 3.00 -- yalnızca bir satır kazanabilir (m1 seçimleri dışlayıcı)
    assert result.max_gain == Decimal("600.00")
    picked = {p.event_id: p.selection_id for p in result.best_scenario}
    assert picked["m1"] == "o1"  # 4.00 oranlı seçim


def test_system_2_of_3_sums_all_winning_lines():
    """2/3 sistem: 3 satır, en iyi senaryoda 3'ü de kazanır."""
    result = calculate_max_gain(
        coupon(ev("m1", "2.00"), ev("m2", "3.00"), ev("m3", "4.00"), system_sizes=(2,))
    )
    assert result.line_count == 3
    per_line = Decimal("100") / 3
    expected = per_line * (Decimal("6") + Decimal("8") + Decimal("12"))  # e_2 = 26
    # ödemeler 2 ondalığa aşağı yuvarlanır
    assert result.max_gain == expected.quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    assert result.max_gain == Decimal("866.66")
    assert result.warnings  # 100 TL 3 satıra tam bölünmüyor


def test_banker_is_present_in_every_line():
    """Banko event her satırda; sistem yalnızca kalan event'lere uygulanır."""
    result = calculate_max_gain(
        coupon(
            ev("b1", "1.50", banker=True),
            ev("m1", "2.00"),
            ev("m2", "3.00"),
            system_sizes=(1,),
            stake="10.00",
            stake_mode="per_line",
        )
    )
    assert result.line_count == 2  # 1/2 sistem -> 2 satır, ikisinde de banko var
    assert result.total_stake == Decimal("20.00")
    # 10*1.5*2 + 10*1.5*3 = 30 + 45
    assert result.max_gain == Decimal("75.00")


def test_multi_size_system_combines_sizes():
    """[2,3] sistem: hem 2'li hem 3'lü satırlar üretilir ve toplanır."""
    result = calculate_max_gain(
        coupon(
            ev("m1", "2.00"),
            ev("m2", "3.00"),
            ev("m3", "4.00"),
            system_sizes=(2, 3),
            stake="1.00",
            stake_mode="per_line",
        )
    )
    assert result.line_count == 4  # C(3,2) + C(3,3)
    # e_2 = 26, e_3 = 24
    assert result.max_gain == Decimal("50.00")
    assert [b.system_size for b in result.breakdown] == [2, 3]
    assert [b.line_count for b in result.breakdown] == [3, 1]


def test_max_single_line_gain_is_best_individual_line():
    result = calculate_max_gain(
        coupon(
            ev("m1", "2.00"),
            ev("m2", "3.00"),
            ev("m3", "10.00"),
            system_sizes=(2,),
            stake="1.00",
            stake_mode="per_line",
        )
    )
    assert result.max_single_line_gain == Decimal("30.00")  # 1 * 3.00 * 10.00
    assert result.max_gain == Decimal("56.00")  # 6 + 20 + 30


# --------------------------------------------------------------------------- #
# Bonus ve ödeme tavanı
# --------------------------------------------------------------------------- #


def test_bonus_multiplier_scales_payout():
    result = calculate_max_gain(
        coupon(ev("m1", "2.00"), ev("m2", "3.00"), bonus_multiplier=Decimal("1.10"))
    )
    assert result.max_gain == Decimal("660.00")  # 100 * 6 * 1.1


def test_payout_cap_clamps_result_and_warns():
    result = calculate_max_gain(
        coupon(ev("m1", "10.00"), ev("m2", "10.00"), max_payout_cap=Decimal("5000.00"))
    )
    assert result.capped is True
    assert result.max_gain == Decimal("5000.00")
    assert result.max_single_line_gain == Decimal("5000.00")
    assert any("tavan" in w for w in result.warnings)


# --------------------------------------------------------------------------- #
# Doğrulama
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("kwargs", "fragment"),
    [
        ({"system_sizes": (4,)}, "Geçersiz sistem boyutu"),
        ({"system_sizes": (0,)}, "Geçersiz sistem boyutu"),
        ({"stake": Decimal("0")}, "Stake"),
        ({"stake_mode": "nope"}, "stake_mode"),
    ],
)
def test_invalid_coupons_raise(kwargs, fragment):
    events = (ev("m1", "2.00"), ev("m2", "3.00"))
    base = {"events": events, "stake": Decimal("100")}
    base.update(kwargs)
    with pytest.raises(CouponError, match=fragment):
        calculate_max_gain(CouponInput(**base))


def test_duplicate_event_ids_rejected():
    with pytest.raises(CouponError, match="Tekrar eden event id"):
        calculate_max_gain(coupon(ev("m1", "2.00"), ev("m1", "3.00")))


def test_odds_must_exceed_one():
    with pytest.raises(CouponError, match="1.00'den büyük"):
        calculate_max_gain(coupon(ev("m1", "1.00")))


def test_all_banker_coupon_needs_no_system():
    result = calculate_max_gain(
        coupon(ev("b1", "2.00", banker=True), ev("b2", "3.00", banker=True))
    )
    assert result.line_count == 1
    assert result.max_gain == Decimal("600.00")


def test_system_with_all_bankers_rejected():
    with pytest.raises(CouponError, match="Tüm event'ler banko"):
        calculate_max_gain(coupon(ev("b1", "2.00", banker=True), system_sizes=(1,)))


# --------------------------------------------------------------------------- #
# Kaba kuvvet çapraz doğrulama -- polinom kısayolunun ana güvencesi
# --------------------------------------------------------------------------- #


def _random_coupon(rng: random.Random) -> CouponInput:
    event_count = rng.randint(1, 5)
    events = []
    for i in range(event_count):
        selection_count = rng.randint(1, 3)
        odds = [Decimal(str(round(rng.uniform(1.05, 6.0), 2))) for _ in range(selection_count)]
        events.append(ev(f"m{i}", *(str(o) for o in odds), banker=rng.random() < 0.25))

    combinable_count = sum(1 for e in events if not e.banker)
    if combinable_count and rng.random() < 0.7:
        pool = range(1, combinable_count + 1)
        sizes = tuple(sorted(rng.sample(list(pool), rng.randint(1, len(pool)))))
    else:
        sizes = None

    stake_mode = rng.choice(["total", "per_line"])
    stake = Decimal(str(rng.choice([1, 5, 10, 100, 250])))
    return CouponInput(events=tuple(events), stake=stake, stake_mode=stake_mode, system_sizes=sizes)


@pytest.mark.parametrize("seed", range(120))
def test_matches_brute_force_reference(seed):
    """Polinom kısayolu, satırları tek tek üreten referansla birebir aynı olmalı."""
    rng = random.Random(seed)
    c = _random_coupon(rng)

    try:
        result = calculate_max_gain(c)
    except CouponError:
        pytest.skip("rastgele üretilen kupon geçersiz")

    expected_gain, expected_lines = brute_force_max_gain(c)
    assert result.line_count == expected_lines
    assert result.max_gain == expected_gain


def test_sub_cent_stake_per_line_is_not_rounded_to_zero():
    """Büyük sistemlerde satır başı stake kuruşun altına iner; 0.00'a kırpılmamalı."""
    events = [ev(f"m{i}", "2.00") for i in range(20)]
    result = calculate_max_gain(coupon(*events, system_sizes=(4,), stake="10.00"))
    assert result.line_count == 4845  # C(20,4)
    assert result.stake_per_line > 0
    assert result.stake_per_line == Decimal("0.002063")  # 10/4845, aşağı yuvarlanmış
    assert "0.002063" in result.warnings[0]  # uyarı ile alan aynı değeri göstermeli
