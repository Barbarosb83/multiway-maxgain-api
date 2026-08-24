"""Çeyrek / periyot piyasaları ve kombine piyasalar."""

from __future__ import annotations

from decimal import Decimal

from app.services.max_gain import CouponInput, SelectionInput, calculate_max_gain
from app.services.odd_types import ODD_TYPE_MARKET

# Gerçek katalogdan (pre-match uzayı)
MS = 1565  # 3way
AU = 1500  # Over/Under
Q1_HANDIKAP = 1752  # 1st Quarter - Points Spread
Q1_TOTAL = 1748  # 1st Quarter - Total Spread
Q1_1X2 = 1740  # 1st Quarter 1X2
KOMBINE = 1531  # Matchbet + Totals
DC_TOTAL = 1898  # Double Chance and Total
EV_VEYA_KG = 2006  # Home Or Both Teams To Score
P1_TOTAL_LIVE = 40  # Total for first period


def sel(
    odd_type_id: int,
    outcome: str,
    odds: str,
    special: str | None = None,
    is_live: int = 0,
    match_id: str = "m1",
) -> SelectionInput:
    return SelectionInput(
        match_id=match_id,
        odd_type_id=odd_type_id,
        outcome=outcome,
        odds=Decimal(odds),
        special_bet_value=special,
        is_live=is_live,
    )


def weight_of(*selections: SelectionInput):
    result = calculate_max_gain(CouponInput(selections=selections, coupon_amount=Decimal("100")))
    return result.matches[0], result.warnings


# --------------------------------------------------------------------------- #
# Çeyrek ve periyot piyasaları
# --------------------------------------------------------------------------- #


def test_quarter_markets_are_mapped():
    assert ODD_TYPE_MARKET[(0, Q1_1X2)] == "Q1_1X2"
    assert ODD_TYPE_MARKET[(0, Q1_HANDIKAP)] == "Q1_HANDIKAP"
    assert ODD_TYPE_MARKET[(0, Q1_TOTAL)] == "Q1_ALT_UST"
    assert ODD_TYPE_MARKET[(1, P1_TOTAL_LIVE)] == "P1_ALT_UST"


def test_same_quarter_markets_share_one_constraint_group():
    """1. çeyreğin handikabı ile toplamı aynı skoru ölçer; çelişebilirler."""
    compatible, _ = weight_of(
        sel(Q1_HANDIKAP, "1", "2.00", "0:5"),  # ev 6+ farkla önde
        sel(Q1_TOTAL, "Under", "1.90", "20.5"),  # toplam 20'den az
    )
    assert compatible.weight == Decimal("3.90")  # 6-0 mümkün -> toplandı
    assert len(compatible.groups) == 1

    contradictory, _ = weight_of(
        sel(Q1_HANDIKAP, "1", "2.00", "0:25"),  # ev 26+ farkla önde
        sel(Q1_TOTAL, "Under", "1.90", "20.5"),  # ama toplam 20'den az
    )
    assert contradictory.weight == Decimal("2.00")  # imkansız -> max


def test_quarter_and_full_time_are_split_with_a_warning():
    """Çeyrek ile maç sonu tek uzayda ifade edilemez; ayrı ayrı çözülür."""
    match, warnings = weight_of(
        sel(MS, "1", "2.00"),
        sel(Q1_1X2, "1", "3.00"),
    )
    assert match.weight == Decimal("5.00")  # bağımsız sayıldı -> toplandı
    assert {g.group for g in match.groups} == {"SCORE:FT", "SCORE:Q1"}
    assert any("periyotlar ayrı ayrı" in w for w in warnings)


def test_quarter_group_reports_its_own_scoreline():
    match, _ = weight_of(sel(Q1_1X2, "1", "3.00"))
    assert match.groups[0].scoreline is not None
    assert "quarter_1" in match.groups[0].scoreline


# --------------------------------------------------------------------------- #
# Kombine piyasalar
# --------------------------------------------------------------------------- #


def test_combo_market_agrees_with_its_components():
    """'Over and home' hem toplam hem sonuç kısıtı taşır."""
    agreeing, _ = weight_of(
        sel(KOMBINE, "Over and home", "3.50", "2.5"),
        sel(MS, "1", "2.00"),
    )
    assert agreeing.weight == Decimal("5.50")

    wrong_result, _ = weight_of(
        sel(KOMBINE, "Over and home", "3.50", "2.5"),
        sel(MS, "2", "3.00"),
    )
    assert wrong_result.weight == Decimal("3.50")

    wrong_total, _ = weight_of(
        sel(KOMBINE, "Over and home", "3.50", "2.5"),
        sel(AU, "Under", "1.60", "1.5"),
    )
    assert wrong_total.weight == Decimal("3.50")


def test_combo_component_order_does_not_matter():
    """Katalog hem 'Over and home' hem 'home and over' biçimini kullanıyor."""
    first, _ = weight_of(sel(KOMBINE, "Over and home", "3.50", "2.5"), sel(MS, "1", "2.00"))
    second, _ = weight_of(sel(KOMBINE, "home and Over", "3.50", "2.5"), sel(MS, "1", "2.00"))
    assert first.weight == second.weight == Decimal("5.50")


def test_double_chance_combo_uses_slash_separator():
    """'DrawAway / Over' -> (berabere veya deplasman) ve toplam üstü."""
    compatible, _ = weight_of(
        sel(DC_TOTAL, "DrawAway / Over", "2.80", "2.5"),
        sel(MS, "2", "3.00"),
    )
    assert compatible.weight == Decimal("5.80")

    contradictory, _ = weight_of(
        sel(DC_TOTAL, "DrawAway / Over", "2.80", "2.5"),
        sel(MS, "1", "2.00"),
    )
    assert contradictory.weight == Decimal("2.80")


def test_or_market_is_a_union_not_an_intersection():
    """'Ev kazanır VEYA karşılıklı gol' -- ikisinden biri yeterli."""
    with_home_win, _ = weight_of(
        sel(EV_VEYA_KG, "Yes", "1.40"),
        sel(MS, "1", "2.00"),
    )
    assert with_home_win.weight == Decimal("3.40")  # ev galibiyeti tek başına yeter

    # "No" ise ev kazanmamalı ve karşılıklı gol olmamalı
    negated, _ = weight_of(
        sel(EV_VEYA_KG, "No", "2.60"),
        sel(MS, "1", "2.00"),
    )
    assert negated.weight == Decimal("2.60")
