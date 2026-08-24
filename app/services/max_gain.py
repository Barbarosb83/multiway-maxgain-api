"""Multiway / sistem kuponları için maksimum kazanç hesaplama motoru.

Matematiksel temel
------------------
Kupon ``M`` adet event içerir. Her event için birbirini dışlayan birden fazla
seçim (outcome) işaretlenebilir -- "multiway". Sistem tanımı, banko olmayan
event'lerden ``k``'lı alt kümeler üreterek satırları (line/way) oluşturur;
banko event'ler her satırda zorunlu olarak yer alır.

Bir satır = (banko event'lerin her birinden bir seçim)
          + (seçilen k'lı alt kümedeki her event'ten bir seçim)

Toplam satır sayısı bu yüzden kombinatoryal olarak patlar. Ancak max gain'i
bulmak için satırları tek tek üretmek gerekmez:

1. En iyi senaryoda her event'te *en yüksek oranlı* seçim gerçekleşir. Bir
   event'te hangi seçimin tuttuğu, o event'i içeren satırların yalnızca
   ödemesini etkiler; başka satırları geçersiz kılmaz. Oranlar pozitif
   olduğundan her event'te maksimum oranı seçmek toplamı zayıf anlamda
   domine eder.

2. Realizasyon sabitlendiğinde, ``k`` boyutlu her alt küme için tam olarak bir
   satır kazanır ve ödemesi o alt kümedeki oranların çarpımıdır. Dolayısıyla
   tüm kazanan satırların toplamı, oranların **elementer simetrik polinomu**
   ``e_k``'ya eşittir:

       max_gain = satır_stake x (Π banko oranları) x Σ_k e_k(en_yüksek_oranlar)

   Aynı özdeşlik seçim *sayıları* ile kullanıldığında satır sayısını verir:

       satır_sayısı = (Π banko seçim sayıları) x Σ_k e_k(seçim_sayıları)

``e_k`` standart DP ile O(M^2) hesaplanır; 20 bacaklı bir 3/20 sistem bile
mikrosaniyeler mertebesinde çözülür.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, localcontext

__all__ = [
    "CouponInput",
    "EventInput",
    "SelectionInput",
    "MaxGainResult",
    "SizeBreakdown",
    "BestPick",
    "CouponError",
    "calculate_max_gain",
    "elementary_symmetric",
]

# Ara çarpımlar 20+ bacaklı kuponlarda büyüyebildiği için varsayılan 28 haneli
# Decimal hassasiyeti yerine daha geniş bir bağlam kullanılır.
_PRECISION = 60
_MONEY_EXP = Decimal("0.01")
# Satır başı stake ödenen bir tutar değil, toplam stake'in satırlara bölünmüş
# hâlidir; büyük sistemlerde kuruşun altına inebildiği için daha hassas tutulur.
_STAKE_EXP = Decimal("0.000001")


class CouponError(ValueError):
    """Kupon yapısı tutarsız olduğunda fırlatılır."""


# --------------------------------------------------------------------------- #
# Girdi modelleri (framework'ten bağımsız saf veri sınıfları)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SelectionInput:
    id: str
    odds: Decimal
    name: str | None = None


@dataclass(frozen=True)
class EventInput:
    id: str
    selections: tuple[SelectionInput, ...]
    name: str | None = None
    banker: bool = False


@dataclass(frozen=True)
class CouponInput:
    events: tuple[EventInput, ...]
    stake: Decimal
    stake_mode: str = "total"  # "total" | "per_line"
    system_sizes: tuple[int, ...] | None = None
    bonus_multiplier: Decimal | None = None
    max_payout_cap: Decimal | None = None
    currency: str = "TRY"


# --------------------------------------------------------------------------- #
# Çıktı modelleri
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class BestPick:
    event_id: str
    selection_id: str
    odds: Decimal
    banker: bool
    event_name: str | None = None
    selection_name: str | None = None


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
    best_scenario: tuple[BestPick, ...]
    breakdown: tuple[SizeBreakdown, ...]
    warnings: tuple[str, ...]


# --------------------------------------------------------------------------- #
# Çekirdek yardımcılar
# --------------------------------------------------------------------------- #


def elementary_symmetric(values: list[Decimal]) -> list[Decimal]:
    """``e[k]`` = ``values`` içinden seçilen tüm k'lı alt kümelerin çarpımları toplamı.

    ``e[0]`` daima 1'dir (boş çarpım). Dönen listenin uzunluğu ``len(values) + 1``.
    O(n^2) zaman, O(n) ek bellek.
    """
    e = [Decimal(0)] * (len(values) + 1)
    e[0] = Decimal(1)
    for i, value in enumerate(values, start=1):
        for k in range(i, 0, -1):
            e[k] += e[k - 1] * value
    return e


def _elementary_symmetric_int(values: list[int]) -> list[int]:
    """``elementary_symmetric``'in tamsayı sürümü -- satır sayısı tam olmalı."""
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
# Doğrulama
# --------------------------------------------------------------------------- #


def _validate(coupon: CouponInput) -> None:
    if not coupon.events:
        raise CouponError("Kupon en az bir event içermelidir.")

    if coupon.stake <= 0:
        raise CouponError("Stake sıfırdan büyük olmalıdır.")

    if coupon.stake_mode not in ("total", "per_line"):
        raise CouponError("stake_mode yalnızca 'total' veya 'per_line' olabilir.")

    seen_events: set[str] = set()
    for event in coupon.events:
        if event.id in seen_events:
            raise CouponError(f"Tekrar eden event id: {event.id!r}")
        seen_events.add(event.id)

        if not event.selections:
            raise CouponError(f"{event.id!r} event'i en az bir seçim içermelidir.")

        seen_selections: set[str] = set()
        for selection in event.selections:
            if selection.id in seen_selections:
                raise CouponError(f"{event.id!r} event'inde tekrar eden seçim id: {selection.id!r}")
            seen_selections.add(selection.id)

            if selection.odds <= 1:
                raise CouponError(
                    f"{event.id}/{selection.id} oranı 1.00'den büyük olmalıdır "
                    f"(gelen: {selection.odds})."
                )

    if coupon.bonus_multiplier is not None and coupon.bonus_multiplier <= 0:
        raise CouponError("bonus_multiplier sıfırdan büyük olmalıdır.")

    if coupon.max_payout_cap is not None and coupon.max_payout_cap <= 0:
        raise CouponError("max_payout_cap sıfırdan büyük olmalıdır.")


def _resolve_system_sizes(coupon: CouponInput, combinable_count: int) -> tuple[int, ...]:
    """Sistem boyutlarını doğrula; verilmemişse tam kombine (full parlay) varsay."""
    if coupon.system_sizes is None:
        return (combinable_count,)

    if not coupon.system_sizes:
        raise CouponError("system.sizes boş olamaz; alanı tümüyle çıkarın.")

    sizes = tuple(sorted(set(coupon.system_sizes)))

    if combinable_count == 0:
        if sizes != (0,):
            raise CouponError(
                "Tüm event'ler banko olduğunda sistem tanımlanamaz; system alanını çıkarın."
            )
        return sizes

    for size in sizes:
        if size < 1 or size > combinable_count:
            raise CouponError(
                f"Geçersiz sistem boyutu {size}: banko olmayan {combinable_count} "
                f"event için 1 ile {combinable_count} arasında olmalıdır."
            )
    return sizes


# --------------------------------------------------------------------------- #
# Ana hesaplama
# --------------------------------------------------------------------------- #


def calculate_max_gain(coupon: CouponInput) -> MaxGainResult:
    """Kuponun en iyi senaryodaki toplam ödemesini (max gain) hesaplar."""
    _validate(coupon)

    with localcontext() as ctx:
        ctx.prec = _PRECISION
        return _calculate(coupon)


def _calculate(coupon: CouponInput) -> MaxGainResult:
    warnings: list[str] = []

    bankers = [e for e in coupon.events if e.banker]
    combinables = [e for e in coupon.events if not e.banker]
    sizes = _resolve_system_sizes(coupon, len(combinables))

    # 1) Her event'te en yüksek oranlı seçim -> en iyi senaryo.
    best_by_event = {e.id: max(e.selections, key=lambda s: s.odds) for e in coupon.events}

    banker_odds = _product([best_by_event[e.id].odds for e in bankers])
    banker_lines = 1
    for event in bankers:
        banker_lines *= len(event.selections)

    # 2) Elementer simetrik polinomlar: biri para, biri satır sayısı için.
    combinable_odds = [best_by_event[e.id].odds for e in combinables]
    combinable_counts = [len(e.selections) for e in combinables]
    e_odds = elementary_symmetric(combinable_odds)
    e_counts = _elementary_symmetric_int(combinable_counts)

    # 3) Sistem boyutu başına satır sayısı ve brüt çarpan.
    lines_per_size = {k: banker_lines * e_counts[k] for k in sizes}
    factor_per_size = {k: banker_odds * e_odds[k] for k in sizes}

    total_lines = sum(lines_per_size.values())
    if total_lines <= 0:  # savunma amaçlı; doğrulama bunu zaten engeller
        raise CouponError("Kupon hiçbir satır üretmiyor.")

    # 4) Stake dağıtımı.
    if coupon.stake_mode == "per_line":
        stake_per_line = coupon.stake
        total_stake = coupon.stake * total_lines
    else:
        total_stake = coupon.stake
        stake_per_line = total_stake / Decimal(total_lines)
        if _quantize_money(stake_per_line) * total_lines != total_stake:
            warnings.append(
                f"Toplam stake {total_lines} satıra tam bölünmüyor; satır başı stake "
                f"{stake_per_line.quantize(_STAKE_EXP, rounding=ROUND_DOWN)} olarak raporlandı."
            )

    # 5) Brüt max gain.
    gross_per_size = {k: stake_per_line * factor_per_size[k] for k in sizes}
    gross_total = sum(gross_per_size.values(), Decimal(0))

    # 6) Bonus çarpanı ve maksimum ödeme tavanı.
    multiplier = coupon.bonus_multiplier if coupon.bonus_multiplier is not None else Decimal(1)
    payout = gross_total * multiplier

    capped = False
    if coupon.max_payout_cap is not None and payout > coupon.max_payout_cap:
        payout = coupon.max_payout_cap
        capped = True
        warnings.append(f"Max gain, {coupon.max_payout_cap} tutarındaki ödeme tavanına takıldı.")

    # 7) Tek satırdan gelebilecek en yüksek ödeme (bilgi amaçlı).
    top_odds = sorted(combinable_odds, reverse=True)
    best_single_factor = Decimal(0)
    for k in sizes:
        factor = banker_odds * _product(top_odds[:k])
        best_single_factor = max(best_single_factor, factor)
    max_single_line = stake_per_line * best_single_factor * multiplier
    if capped and max_single_line > coupon.max_payout_cap:  # type: ignore[operator]
        max_single_line = coupon.max_payout_cap  # type: ignore[assignment]

    # 8) Sunum için yuvarlama.
    max_gain = _quantize_money(payout)
    total_stake_q = _quantize_money(total_stake, rounding=ROUND_HALF_UP)

    breakdown = tuple(
        SizeBreakdown(
            system_size=k,
            line_count=lines_per_size[k],
            gross_gain=_quantize_money(gross_per_size[k] * multiplier),
        )
        for k in sizes
    )

    best_scenario = tuple(
        BestPick(
            event_id=event.id,
            event_name=event.name,
            selection_id=best_by_event[event.id].id,
            selection_name=best_by_event[event.id].name,
            odds=best_by_event[event.id].odds,
            banker=event.banker,
        )
        for event in coupon.events
    )

    return MaxGainResult(
        currency=coupon.currency,
        total_stake=total_stake_q,
        stake_per_line=stake_per_line.quantize(_STAKE_EXP, rounding=ROUND_DOWN),
        line_count=total_lines,
        max_gain=max_gain,
        net_profit=_quantize_money(payout - total_stake, rounding=ROUND_HALF_UP),
        max_single_line_gain=_quantize_money(max_single_line),
        effective_multiplier=(
            (payout / total_stake).quantize(Decimal("0.0001")) if total_stake > 0 else Decimal(0)
        ),
        capped=capped,
        best_scenario=best_scenario,
        breakdown=breakdown,
        warnings=tuple(warnings),
    )
