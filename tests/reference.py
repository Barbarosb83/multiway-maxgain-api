"""Kaba kuvvet referans uygulaması -- yalnızca testlerde kullanılır.

Tüm satırları ve tüm sonuç senaryolarını açıkça üretip max gain'i arar.
Üstel karmaşıklıkta olduğu için sadece küçük kuponlarda çalıştırılabilir; amacı
``app.services.max_gain`` içindeki polinom kısayolunu bağımsız doğrulamaktır.
"""

from __future__ import annotations

import itertools
from decimal import ROUND_DOWN, Decimal, localcontext

from app.services.max_gain import CouponInput


def brute_force_max_gain(coupon: CouponInput) -> tuple[Decimal, int]:
    """(max_gain, satır_sayısı) döner. Bonus/cap uygulanmaz."""
    with localcontext() as ctx:
        ctx.prec = 60
        return _brute(coupon)


def _brute(coupon: CouponInput) -> tuple[Decimal, int]:
    bankers = [e for e in coupon.events if e.banker]
    combinables = [e for e in coupon.events if not e.banker]
    sizes = (
        tuple(sorted(set(coupon.system_sizes)))
        if coupon.system_sizes is not None
        else (len(combinables),)
    )

    # Her satır: {event_id: selection}
    lines: list[dict[str, object]] = []
    for size in sizes:
        for subset in itertools.combinations(combinables, size):
            events_in_line = bankers + list(subset)
            if not events_in_line:
                continue
            for combo in itertools.product(*(e.selections for e in events_in_line)):
                lines.append({e.id: s for e, s in zip(events_in_line, combo, strict=True)})

    line_count = len(lines)
    if line_count == 0:
        raise AssertionError("Referans uygulama hiç satır üretmedi.")

    if coupon.stake_mode == "per_line":
        stake_per_line = coupon.stake
    else:
        stake_per_line = coupon.stake / Decimal(line_count)

    best = Decimal(0)
    # Her event'te tam olarak bir sonuç gerçekleşir; tüm senaryoları tara.
    for realization in itertools.product(*(e.selections for e in coupon.events)):
        realized = {e.id: s for e, s in zip(coupon.events, realization, strict=True)}
        total = Decimal(0)
        for line in lines:
            if all(realized[event_id].id == sel.id for event_id, sel in line.items()):
                payout = stake_per_line
                for sel in line.values():
                    payout *= sel.odds
                total += payout
        best = max(best, total)

    return best.quantize(Decimal("0.01"), rounding=ROUND_DOWN), line_count
