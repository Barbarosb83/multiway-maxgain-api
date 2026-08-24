"""Kaba kuvvet referans uygulaması -- yalnızca testlerde kullanılır.

Motorun hiçbir kısayolunu paylaşmaz:

* uyumlu alt küme aramasını (budamalı DFS) kullanmaz; bir maçtaki kazanan
  seçim kümelerini sonuç uzayının **her atomunu** tek tek deneyerek bulur,
* elementer simetrik polinomu kullanmaz; tüm satırları açıkça üretir ve tüm
  sonuç senaryolarını dolaşarak yalnızca tanımı uygular::

      max_gain = max ( Σ kazanan satırların ödemesi )
                 senaryolar

Üstel karmaşıklıkta olduğu için sadece küçük kuponlarda çalıştırılabilir ve
sabit bir skor uzayı (``HALVES``, tavan 6) varsayar; testler bu sınır içinde
kalan kuponlar üretir.
"""

from __future__ import annotations

import itertools
from collections import OrderedDict
from decimal import ROUND_DOWN, Decimal, localcontext

from app.services.markets import UnknownOutcome, get_space, mask_for, required_bound
from app.services.max_gain import CouponInput, SelectionInput
from app.services.odd_types import resolve_odd_type

REFERENCE_BOUND = 6
_SPACE = get_space("HALVES", REFERENCE_BOUND)


def _selection_mask(selection: SelectionInput) -> int | None:
    """Seçimin kazandığı atom maskesi; çözümlenemiyorsa None (yalıtılır)."""
    info = resolve_odd_type(selection.odd_type_id, selection.is_live)
    if info.market is None:
        return None
    try:
        if required_bound(info.market.id, selection.outcome, selection.special_bet_value) > (
            REFERENCE_BOUND
        ):
            return None
        mask = mask_for(info.market.id, selection.outcome, selection.special_bet_value, _SPACE.key)
    except UnknownOutcome:
        return None
    return mask or None


def _match_scenarios(selections: list[SelectionInput]) -> list[frozenset[int]]:
    """Bir maçta gerçekleşebilecek *farklı* kazanan-seçim kümeleri."""
    scored: list[tuple[int, int]] = []
    isolated: OrderedDict[str, list[int]] = OrderedDict()

    for index, selection in enumerate(selections):
        mask = _selection_mask(selection)
        if mask is None:
            key = f"iso:{selection.is_live}:{selection.odd_type_id}"
            isolated.setdefault(key, []).append(index)
        else:
            scored.append((index, mask))

    if scored:
        score_subsets = {
            frozenset(index for index, mask in scored if mask & (1 << atom))
            for atom in range(len(_SPACE.atoms))
        }
    else:
        score_subsets = {frozenset()}

    isolated_choices = [
        [frozenset()] + [frozenset({index}) for index in indexes] for indexes in isolated.values()
    ]

    scenarios: set[frozenset[int]] = set()
    for base in score_subsets:
        for combo in itertools.product(*isolated_choices) if isolated_choices else [()]:
            merged = set(base)
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

    match_order = list(by_match)
    scenarios_per_match = [_match_scenarios(by_match[m]) for m in match_order]

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
