"""Kaba kuvvet referans uygulaması -- yalnızca testlerde kullanılır.

Motorun hiçbir kısayolunu paylaşmaz: tüm satırları açıkça üretir, maçların tüm
sonuç senaryolarını tek tek dolaşır ve her senaryoda kazanan satırların
ödemesini toplar. Yalnızca *tanımı* uygular::

    max_gain = max ( Σ kazanan satırların ödemesi )
               senaryolar

Üstel karmaşıklıkta olduğu için sadece küçük kuponlarda çalıştırılabilir.
"""

from __future__ import annotations

import itertools
from collections import OrderedDict
from decimal import ROUND_DOWN, Decimal, localcontext

from app.services.markets import ATOMS, UnknownOutcome, mask_for
from app.services.max_gain import CouponInput, SelectionInput
from app.services.odd_types import resolve_odd_type


def _match_scenarios(selections: list[SelectionInput]) -> list[frozenset[int]]:
    """Bir maçta gerçekleşebilecek *farklı* kazanan-seçim kümelerini üretir.

    Gol bazlı seçimler için 1296 atomun tamamı taranır; katalogda olmayan
    oddType'lar kendi yalıtılmış grubunda en fazla bir kazanan üretir.
    """
    goal_items: list[tuple[int, int]] = []
    isolated: OrderedDict[str, list[int]] = OrderedDict()

    for index, selection in enumerate(selections):
        info = resolve_odd_type(selection.odd_type_id)
        mask = 0
        if info.market is not None:
            try:
                mask = mask_for(info.market.id, selection.outcome)
            except UnknownOutcome:
                mask = 0
        if mask:
            goal_items.append((index, mask))
        else:
            isolated.setdefault(f"iso:{selection.odd_type_id}", []).append(index)

    if goal_items:
        goal_subsets = {
            frozenset(index for index, mask in goal_items if mask & (1 << atom))
            for atom in range(len(ATOMS))
        }
    else:
        goal_subsets = {frozenset()}

    isolated_choices = [
        [frozenset()] + [frozenset({index}) for index in indexes] for indexes in isolated.values()
    ]

    scenarios: set[frozenset[int]] = set()
    for goals in goal_subsets:
        for combo in itertools.product(*isolated_choices) if isolated_choices else [()]:
            merged = set(goals)
            for choice in combo:
                merged |= choice
            scenarios.add(frozenset(merged))
    return sorted(scenarios, key=sorted)


def brute_force_max_gain(coupon: CouponInput) -> tuple[Decimal, int]:
    """(max_gain, satır_sayısı) döner. Bonus/cap uygulanmaz."""
    with localcontext() as ctx:
        ctx.prec = 60
        return _brute(coupon)


def _brute(coupon: CouponInput) -> tuple[Decimal, int]:
    by_match: OrderedDict[str, list[SelectionInput]] = OrderedDict()
    for selection in coupon.selections:
        by_match.setdefault(selection.match_id, []).append(selection)

    bankers = [m for m in by_match if m in coupon.banker_match_ids]
    combinables = [m for m in by_match if m not in coupon.banker_match_ids]
    sizes = (
        tuple(sorted(set(coupon.system_sizes)))
        if coupon.system_sizes is not None
        else (len(combinables),)
    )

    # Satırlar: her satır, dahil olduğu her maçtan tam olarak bir seçim alır.
    lines: list[tuple[tuple[str, int], ...]] = []
    for size in sizes:
        for subset in itertools.combinations(combinables, size):
            matches_in_line = bankers + list(subset)
            if not matches_in_line:
                continue
            choices = [range(len(by_match[m])) for m in matches_in_line]
            for combo in itertools.product(*choices):
                lines.append(tuple(zip(matches_in_line, combo, strict=True)))

    line_count = len(lines)
    if line_count == 0:
        raise AssertionError("Referans uygulama hiç satır üretmedi.")

    stake_per_line = (
        coupon.coupon_amount
        if coupon.stake_mode == "per_line"
        else coupon.coupon_amount / Decimal(line_count)
    )

    scenarios_per_match = [_match_scenarios(by_match[m]) for m in by_match]
    match_order = list(by_match)

    best = Decimal(0)
    for realization in itertools.product(*scenarios_per_match):
        winners = dict(zip(match_order, realization, strict=True))
        total = Decimal(0)
        for line in lines:
            if all(selection_index in winners[match] for match, selection_index in line):
                payout = stake_per_line
                for match, selection_index in line:
                    payout *= by_match[match][selection_index].odds
                total += payout
        best = max(best, total)

    return best.quantize(Decimal("0.01"), rounding=ROUND_DOWN), line_count
