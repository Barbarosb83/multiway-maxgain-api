"""Multiway / sistem kuponları için maksimum kazanç hesaplama motoru.

Girdi modeli
------------
Kupon düz bir *seçim* listesidir. Her seçim bir maça (``match_id``), bir
piyasaya (``odd_type_id``), bir sonuca (``outcome``) ve bir orana (``odds``)
sahiptir. Aynı maça birden fazla seçim gelebilir -- "multiway".

Maç ağırlığı
------------
Aynı maçtaki iki seçimin birlikte tutup tutamayacağı, oddType'larının farklı
olmasıyla değil, *aynı anda gerçekleşebilir olmalarıyla* belirlenir:

* ``1X2 "1"`` + ``1X2 "X"``   -> kesişim yok  -> yalnızca biri kazanır  -> max
* ``1X2 "1"`` + ``ÇŞ "1X"``   -> ev kazanırsa ikisi de tutar            -> toplam
* ``1X2 "1"`` + ``ÇŞ "X2"``   -> kesişim yok  -> yalnızca biri kazanır  -> max

Bu yüzden maçın ağırlığı, *birlikte gerçekleşebilen* seçim alt kümeleri
arasında oran toplamı en yüksek olanıdır::

    w(maç) = max { Σ odds(S) : S seçim alt kümesi, ∩ S ≠ ∅ }

Uyumluluk ``app.services.markets`` içindeki somut sonuç uzayı üzerinden
hesaplanır; katalogda olmayan oddType'lar için güvenli geri düşüş uygulanır.

Kupon toplamı
-------------
Her satır (line/way), dahil olduğu her maçtan tam olarak bir seçim alır.
Realizasyon sabitlendiğinde ``k`` boyutlu her alt kümeden kazanan satırların
toplamı, maç ağırlıklarının **elementer simetrik polinomu** ``e_k``'ya eşittir::

    max_gain     = satır_stake x (Π banko ağırlıkları) x Σ_k e_k(ağırlıklar)
    satır_sayısı =               (Π banko seçim sayıları) x Σ_k e_k(seçim sayıları)

``e_k`` standart DP ile O(M^2) hesaplanır; satırlar hiçbir zaman tek tek
üretilmez.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, localcontext

from app.services.markets import (
    MAX_SCORE_BOUND,
    UnknownOutcome,
    describe_atom,
    get_space,
    joint_fits,
    mask_for,
    required_bound,
)
from app.services.odd_types import resolve_odd_type

__all__ = [
    "CouponInput",
    "SelectionInput",
    "MaxGainResult",
    "MatchResolution",
    "GroupResolution",
    "WinningSelection",
    "SizeBreakdown",
    "CouponError",
    "calculate_max_gain",
    "elementary_symmetric",
]

_PRECISION = 60
_MONEY_EXP = Decimal("0.01")
# Satır başı stake ödenen bir tutar değil, kupon tutarının satırlara bölünmüş
# hâlidir; büyük sistemlerde kuruşun altına inebildiği için daha hassas tutulur.
_STAKE_EXP = Decimal("0.000001")

MAX_MATCHES = 50
MAX_SELECTIONS_PER_MATCH = 12


class CouponError(ValueError):
    """Kupon yapısı tutarsız olduğunda fırlatılır."""


# --------------------------------------------------------------------------- #
# Girdi modelleri
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SelectionInput:
    match_id: str
    odd_type_id: int
    outcome: str
    odds: Decimal
    is_live: int = 0  # pre-match: 0, live: 1 -- oddTypeId'nin hangi katalogda aranacağı
    special_bet_value: str | None = None  # eşik/handikap, ör. "2.5" veya "0:1"


@dataclass(frozen=True)
class CouponInput:
    selections: tuple[SelectionInput, ...]
    coupon_amount: Decimal
    stake_mode: str = "total"  # "total" | "per_line"
    system_sizes: tuple[int, ...] | None = None
    banker_match_ids: frozenset[str] = frozenset()
    bonus_multiplier: Decimal | None = None
    max_payout_cap: Decimal | None = None
    currency: str = "TRY"


# --------------------------------------------------------------------------- #
# Çıktı modelleri
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class WinningSelection:
    odd_type_id: int
    odd_type_name: str
    is_live: int
    outcome: str
    odds: Decimal
    special_bet_value: str | None = None


@dataclass(frozen=True)
class GroupResolution:
    """Bir maçın tek bir kısıt grubundaki en iyi sonucu."""

    group: str
    odds_sum: Decimal
    combined: bool  # True => birden fazla seçim aynı anda kazanıyor
    winning_selections: tuple[WinningSelection, ...]
    scoreline: dict[str, str] | None


@dataclass(frozen=True)
class MatchResolution:
    match_id: str
    banker: bool
    selection_count: int
    weight: Decimal
    groups: tuple[GroupResolution, ...]


@dataclass(frozen=True)
class SizeBreakdown:
    system_size: int
    line_count: int
    gross_gain: Decimal


@dataclass(frozen=True)
class MaxGainResult:
    currency: str
    total_stake: Decimal
    stake_per_line: Decimal
    line_count: int
    max_gain: Decimal
    net_profit: Decimal
    max_single_line_gain: Decimal
    effective_multiplier: Decimal
    capped: bool
    matches: tuple[MatchResolution, ...]
    breakdown: tuple[SizeBreakdown, ...]
    warnings: tuple[str, ...]


# --------------------------------------------------------------------------- #
# Elementer simetrik polinomlar
# --------------------------------------------------------------------------- #


def elementary_symmetric(values: list[Decimal]) -> list[Decimal]:
    """``e[k]`` = ``values`` içinden seçilen tüm k'lı alt kümelerin çarpımları toplamı."""
    e = [Decimal(0)] * (len(values) + 1)
    e[0] = Decimal(1)
    for i, value in enumerate(values, start=1):
        for k in range(i, 0, -1):
            e[k] += e[k - 1] * value
    return e


def _elementary_symmetric_int(values: list[int]) -> list[int]:
    e = [0] * (len(values) + 1)
    e[0] = 1
    for i, value in enumerate(values, start=1):
        for k in range(i, 0, -1):
            e[k] += e[k - 1] * value
    return e


def _quantize_money(value: Decimal, rounding: str = ROUND_DOWN) -> Decimal:
    return value.quantize(_MONEY_EXP, rounding=rounding)


def _product(values: list[Decimal]) -> Decimal:
    result = Decimal(1)
    for value in values:
        result *= value
    return result


# --------------------------------------------------------------------------- #
# Uyumlu seçim alt kümesi araması
# --------------------------------------------------------------------------- #


def _best_compatible_subset(
    items: list[tuple[Decimal, int]], full_mask: int
) -> tuple[Decimal, tuple[int, ...], int]:
    """Kesişimi boş olmayan, oran toplamı en yüksek alt kümeyi bulur.

    ``items`` her seçim için (oran, atom maskesi) ikilisidir. Dallanma,
    kalan oranların toplamı mevcut en iyiyi geçemiyorsa budanır; pratikte
    çelişen seçimler daha ilk adımda elendiği için arama çok hızlı biter.

    Döner: (en iyi toplam, seçilen indeksler, kesişim maskesi)
    """
    n = len(items)
    order = sorted(range(n), key=lambda i: -items[i][0])

    suffix = [Decimal(0)] * (n + 1)
    for pos in range(n - 1, -1, -1):
        suffix[pos] = suffix[pos + 1] + items[order[pos]][0]

    best_total = Decimal(0)
    best_chosen: tuple[int, ...] = ()
    best_mask = 0

    def dfs(pos: int, intersection: int, total: Decimal, chosen: tuple[int, ...]) -> None:
        nonlocal best_total, best_chosen, best_mask
        if total > best_total:
            best_total, best_chosen, best_mask = total, chosen, intersection
        if pos == n or total + suffix[pos] <= best_total:
            return

        index = order[pos]
        odds, mask = items[index]
        narrowed = intersection & mask
        if narrowed:
            dfs(pos + 1, narrowed, total + odds, (*chosen, index))
        dfs(pos + 1, intersection, total, chosen)

    dfs(0, full_mask, Decimal(0), ())
    return best_total, tuple(sorted(best_chosen)), best_mask


# --------------------------------------------------------------------------- #
# Maç çözümlemesi
# --------------------------------------------------------------------------- #


def _quantize_bound(bound: int) -> int:
    """Skor tavanını basamaklara oturtur; aynı uzay tekrar tekrar kurulmasın."""
    step = 4 if bound <= 32 else 16
    return min(MAX_SCORE_BOUND, ((bound + step - 1) // step) * step)


@dataclass(frozen=True)
class _Resolved:
    """Anlamı çözümlenmiş tek bir seçim."""

    selection: SelectionInput
    market_id: str
    period: str
    bound: int


def _plan_space(periods: set[str], bound: int) -> tuple[str, bool]:
    """(uzay türü, bölme gerekli mi) döner.

    Tek periyotluk gruplar iki boyutlu uzayda çözülür ve büyük tavanlara
    (basketbol, kriket) izin verir. Birden fazla periyot karışıyorsa dört
    boyutlu ortak uzay gerekir; o da atom sınırına sığmıyorsa grup periyotlara
    bölünür.
    """
    if periods <= {"FT"} or periods <= {"2H"}:
        return "FLAT", False
    if periods <= {"HT"}:
        return "HALF", False
    return ("JOINT", False) if joint_fits(bound) else ("JOINT", True)


def _solve_group(
    entries: list[tuple[SelectionInput, int]], space_key: tuple[str, int]
) -> tuple[Decimal, list[SelectionInput], dict[str, str] | None]:
    """Bir kısıt grubundaki en iyi uyumlu alt kümeyi çözer."""
    space = get_space(*space_key)
    items = [(selection.odds, mask) for selection, mask in entries]
    total, chosen, final_mask = _best_compatible_subset(items, space.full_mask)
    winners = [entries[index][0] for index in chosen]
    scoreline = describe_atom(space, (final_mask & -final_mask).bit_length() - 1)
    return total, winners, scoreline


def _resolve_match(
    match_id: str, selections: list[SelectionInput], banker: bool, warnings: list[str]
) -> MatchResolution:
    """Bir maçtaki seçimleri kısıt gruplarına ayırıp ağırlığını hesaplar."""
    resolved: list[_Resolved] = []
    isolated: OrderedDict[str, list[SelectionInput]] = OrderedDict()

    def isolate(selection: SelectionInput, reason: str) -> None:
        key = f"UNMAPPED:{selection.is_live}:{selection.odd_type_id}"
        isolated.setdefault(key, []).append(selection)
        warnings.append(f"Maç {match_id}: {reason}")

    for selection in selections:
        info = resolve_odd_type(selection.odd_type_id, selection.is_live)
        source = "live" if selection.is_live else "pre"

        if info.market is None:
            reason = "anlamı eşlenmemiş" if info.in_catalog else f"{source} katalogunda yok"
            isolate(
                selection,
                f"oddType {selection.odd_type_id} ({source}) {reason}; aynı id'nin "
                "seçimleri dışlayıcı, farklı id'ler bağımsız sayıldı.",
            )
            continue

        try:
            bound = required_bound(info.market.id, selection.outcome, selection.special_bet_value)
        except UnknownOutcome as exc:
            isolate(selection, f"{exc} Seçim yalıtılmış olarak değerlendirildi.")
            continue

        resolved.append(
            _Resolved(
                selection=selection,
                market_id=info.market.id,
                period=info.market.period,
                bound=bound,
            )
        )

    groups: OrderedDict[str, list[tuple[SelectionInput, int]]] = OrderedDict()
    space_keys: dict[str, tuple[str, int]] = {}

    if resolved:
        bound = _quantize_bound(max(item.bound for item in resolved))
        periods = {item.period for item in resolved}
        kind, must_split = _plan_space(periods, bound)

        if must_split:
            warnings.append(
                f"Maç {match_id}: skor tavanı {bound} ile ilk yarı ve maç sonu birlikte "
                "modellenemeyecek kadar büyük; periyotlar ayrı ayrı değerlendirildi "
                "(periyotlar arası çelişkiler tespit edilemez)."
            )
            buckets: OrderedDict[str, list[_Resolved]] = OrderedDict()
            for item in resolved:
                buckets.setdefault(item.period, []).append(item)
        else:
            buckets = OrderedDict({"": resolved})

        for period, items in buckets.items():
            group_bound = _quantize_bound(max(item.bound for item in items))
            group_kind = kind if not must_split else ("HALF" if period == "HT" else "FLAT")
            space_key = (group_kind, group_bound)
            name = "SCORE" if not period else f"SCORE:{period}"

            for item in items:
                try:
                    mask = mask_for(
                        item.market_id,
                        item.selection.outcome,
                        item.selection.special_bet_value,
                        space_key,
                    )
                except UnknownOutcome as exc:
                    isolate(item.selection, f"{exc} Seçim yalıtılmış olarak değerlendirildi.")
                    continue
                if mask:
                    groups.setdefault(name, []).append((item.selection, mask))
                    space_keys[name] = space_key
                else:
                    isolate(
                        item.selection,
                        f"oddType {item.selection.odd_type_id} / "
                        f"{item.selection.outcome!r} (specialBetValue="
                        f"{item.selection.special_bet_value!r}) modellenen sonuç uzayında "
                        "hiçbir senaryoyla eşleşmiyor; yalıtılmış olarak değerlendirildi.",
                    )

    resolutions: list[GroupResolution] = []
    weight = Decimal(0)

    for name, entries in groups.items():
        total, winners, scoreline = _solve_group(entries, space_keys[name])
        weight += total
        resolutions.append(_group_out(name, total, winners, scoreline))

    for name, entries in isolated.items():
        # Yalıtılmış grup: aynı oddType'ın seçimleri birbirini dışlar.
        best = max(entries, key=lambda selection: selection.odds)
        weight += best.odds
        resolutions.append(_group_out(name, best.odds, [best], None))

    return MatchResolution(
        match_id=match_id,
        banker=banker,
        selection_count=len(selections),
        weight=weight,
        groups=tuple(resolutions),
    )


def _group_out(
    group: str,
    total: Decimal,
    winners: list[SelectionInput],
    scoreline: dict[str, str] | None,
) -> GroupResolution:
    return GroupResolution(
        group=group,
        odds_sum=total,
        combined=len(winners) > 1,
        winning_selections=tuple(
            WinningSelection(
                odd_type_id=w.odd_type_id,
                odd_type_name=resolve_odd_type(w.odd_type_id, w.is_live).name,
                is_live=w.is_live,
                outcome=w.outcome,
                odds=w.odds,
                special_bet_value=w.special_bet_value,
            )
            for w in winners
        ),
        scoreline=scoreline,
    )


# --------------------------------------------------------------------------- #
# Doğrulama
# --------------------------------------------------------------------------- #


def _validate(coupon: CouponInput) -> OrderedDict[str, list[SelectionInput]]:
    if not coupon.selections:
        raise CouponError("Kupon en az bir seçim içermelidir.")

    if coupon.coupon_amount <= 0:
        raise CouponError("Kupon tutarı sıfırdan büyük olmalıdır.")

    if coupon.stake_mode not in ("total", "per_line"):
        raise CouponError("stake_mode yalnızca 'total' veya 'per_line' olabilir.")

    by_match: OrderedDict[str, list[SelectionInput]] = OrderedDict()
    seen: set[tuple[str, int, int, str, str]] = set()

    for selection in coupon.selections:
        if selection.odds <= 1:
            raise CouponError(
                f"Maç {selection.match_id} / oddType {selection.odd_type_id} oranı "
                f"1.00'den büyük olmalıdır (gelen: {selection.odds})."
            )

        key = (
            selection.match_id,
            selection.is_live,
            selection.odd_type_id,
            selection.outcome.strip().upper(),
            (selection.special_bet_value or "").strip().upper(),
        )
        if key in seen:
            raise CouponError(
                f"Aynı seçim iki kez gönderildi: maç {selection.match_id}, "
                f"oddType {selection.odd_type_id}, outcome {selection.outcome!r}."
            )
        seen.add(key)
        by_match.setdefault(selection.match_id, []).append(selection)

    if len(by_match) > MAX_MATCHES:
        raise CouponError(f"Kupon en fazla {MAX_MATCHES} maç içerebilir.")

    for match_id, selections in by_match.items():
        if len(selections) > MAX_SELECTIONS_PER_MATCH:
            raise CouponError(
                f"Maç {match_id} için en fazla {MAX_SELECTIONS_PER_MATCH} seçim gönderilebilir."
            )

    unknown_bankers = coupon.banker_match_ids - set(by_match)
    if unknown_bankers:
        raise CouponError(f"Banko olarak işaretlenen maçlar kuponda yok: {sorted(unknown_bankers)}")

    if coupon.bonus_multiplier is not None and coupon.bonus_multiplier <= 0:
        raise CouponError("bonus_multiplier sıfırdan büyük olmalıdır.")

    if coupon.max_payout_cap is not None and coupon.max_payout_cap <= 0:
        raise CouponError("max_payout_cap sıfırdan büyük olmalıdır.")

    return by_match


def _resolve_system_sizes(coupon: CouponInput, combinable_count: int) -> tuple[int, ...]:
    if coupon.system_sizes is None:
        return (combinable_count,)

    if not coupon.system_sizes:
        raise CouponError("system.sizes boş olamaz; alanı tümüyle çıkarın.")

    sizes = tuple(sorted(set(coupon.system_sizes)))

    if combinable_count == 0:
        if sizes != (0,):
            raise CouponError(
                "Tüm maçlar banko olduğunda sistem tanımlanamaz; system alanını çıkarın."
            )
        return sizes

    for size in sizes:
        if size < 1 or size > combinable_count:
            raise CouponError(
                f"Geçersiz sistem boyutu {size}: banko olmayan {combinable_count} "
                f"maç için 1 ile {combinable_count} arasında olmalıdır."
            )
    return sizes


# --------------------------------------------------------------------------- #
# Ana hesaplama
# --------------------------------------------------------------------------- #


def calculate_max_gain(coupon: CouponInput) -> MaxGainResult:
    """Kuponun en iyi senaryodaki toplam ödemesini (max gain) hesaplar."""
    by_match = _validate(coupon)
    with localcontext() as ctx:
        ctx.prec = _PRECISION
        return _calculate(coupon, by_match)


def _calculate(
    coupon: CouponInput, by_match: OrderedDict[str, list[SelectionInput]]
) -> MaxGainResult:
    warnings: list[str] = []

    matches = tuple(
        _resolve_match(match_id, selections, match_id in coupon.banker_match_ids, warnings)
        for match_id, selections in by_match.items()
    )

    bankers = [m for m in matches if m.banker]
    combinables = [m for m in matches if not m.banker]
    sizes = _resolve_system_sizes(coupon, len(combinables))

    banker_weight = _product([m.weight for m in bankers])
    banker_lines = 1
    for match in bankers:
        banker_lines *= match.selection_count

    e_weights = elementary_symmetric([m.weight for m in combinables])
    e_counts = _elementary_symmetric_int([m.selection_count for m in combinables])

    lines_per_size = {k: banker_lines * e_counts[k] for k in sizes}
    factor_per_size = {k: banker_weight * e_weights[k] for k in sizes}

    total_lines = sum(lines_per_size.values())
    if total_lines <= 0:
        raise CouponError("Kupon hiçbir satır üretmiyor.")

    if coupon.stake_mode == "per_line":
        stake_per_line = coupon.coupon_amount
        total_stake = coupon.coupon_amount * total_lines
    else:
        total_stake = coupon.coupon_amount
        stake_per_line = total_stake / Decimal(total_lines)
        if _quantize_money(stake_per_line) * total_lines != total_stake:
            warnings.append(
                f"Kupon tutarı {total_lines} satıra tam bölünmüyor; satır başı stake "
                f"{stake_per_line.quantize(_STAKE_EXP, rounding=ROUND_DOWN)} olarak raporlandı."
            )

    gross_per_size = {k: stake_per_line * factor_per_size[k] for k in sizes}
    gross_total = sum(gross_per_size.values(), Decimal(0))

    multiplier = coupon.bonus_multiplier if coupon.bonus_multiplier is not None else Decimal(1)
    payout = gross_total * multiplier

    capped = False
    if coupon.max_payout_cap is not None and payout > coupon.max_payout_cap:
        payout = coupon.max_payout_cap
        capped = True
        warnings.append(f"Max gain, {coupon.max_payout_cap} tutarındaki ödeme tavanına takıldı.")

    # Tek satır, her maçtan yalnızca bir seçim alır; bu yüzden ağırlık değil,
    # maçtaki en yüksek tekil oran kullanılır.
    def _top_odds(match: MatchResolution) -> Decimal:
        return max(
            selection.odds for group in match.groups for selection in group.winning_selections
        )

    banker_top = _product([_top_odds(m) for m in bankers])
    combinable_top = sorted((_top_odds(m) for m in combinables), reverse=True)
    best_single_factor = Decimal(0)
    for k in sizes:
        best_single_factor = max(best_single_factor, banker_top * _product(combinable_top[:k]))
    max_single_line = stake_per_line * best_single_factor * multiplier
    if coupon.max_payout_cap is not None and max_single_line > coupon.max_payout_cap:
        max_single_line = coupon.max_payout_cap

    breakdown = tuple(
        SizeBreakdown(
            system_size=k,
            line_count=lines_per_size[k],
            gross_gain=_quantize_money(gross_per_size[k] * multiplier),
        )
        for k in sizes
    )

    return MaxGainResult(
        currency=coupon.currency,
        total_stake=_quantize_money(total_stake, rounding=ROUND_HALF_UP),
        stake_per_line=stake_per_line.quantize(_STAKE_EXP, rounding=ROUND_DOWN),
        line_count=total_lines,
        max_gain=_quantize_money(payout),
        net_profit=_quantize_money(payout - total_stake, rounding=ROUND_HALF_UP),
        max_single_line_gain=_quantize_money(max_single_line),
        effective_multiplier=(
            (payout / total_stake).quantize(Decimal("0.0001")) if total_stake > 0 else Decimal(0)
        ),
        capped=capped,
        matches=matches,
        breakdown=breakdown,
        warnings=tuple(dict.fromkeys(warnings)),
    )
