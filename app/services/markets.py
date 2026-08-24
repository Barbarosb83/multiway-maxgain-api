"""Bahis piyasalarının anlamsal tanımları.

Bir seçimin (oddType + outcome) *ne zaman kazandığını* bilmeden iki seçimin
birlikte tutup tutamayacağı söylenemez. Bu modül her piyasayı, maçın somut
sonuçları üzerinde bir yüklem (predicate) olarak tanımlar.

Atom
----
Gol bazlı tüm piyasalar tek bir ortak sonuç uzayında yaşar::

    Atom = (iy_ev, iy_dep, ms_ev, ms_dep)

yani ilk yarı ve maç sonu skorları birlikte. ``iy <= ms`` kısıtı sayesinde
"İY 2-0" ile "MS toplam 0.5 Alt" gibi çelişkiler kendiliğinden elenir --
bu iki piyasa ayrı ayrı modellenseydi yanlışlıkla uyumlu sayılırdı.

Skorlar ``MAX_GOALS``'a kadar modellenir; bu 36^2 = 1296 atom demektir.
Daha yüksek skorlar hiçbir standart piyasanın sonucunu değiştirmez.

Gol dışı piyasalar (korner, kart vb.) kendi gruplarında yaşar; farklı
gruplar bağımsız kabul edilir ve katkıları toplanır.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache

__all__ = [
    "MAX_GOALS",
    "GOALS_GROUP",
    "Atom",
    "ATOMS",
    "FULL_MASK",
    "MarketDef",
    "MARKETS",
    "UnknownOutcome",
    "mask_for",
    "describe_atom",
]

MAX_GOALS = 7
GOALS_GROUP = "GOALS"

# (iy_ev, iy_dep, ms_ev, ms_dep)
Atom = tuple[int, int, int, int]


def _build_atoms() -> tuple[Atom, ...]:
    return tuple(
        (ht_home, ht_away, ft_home, ft_away)
        for ft_home in range(MAX_GOALS + 1)
        for ft_away in range(MAX_GOALS + 1)
        for ht_home in range(ft_home + 1)
        for ht_away in range(ft_away + 1)
    )


ATOMS: tuple[Atom, ...] = _build_atoms()
FULL_MASK: int = (1 << len(ATOMS)) - 1


class UnknownOutcome(ValueError):
    """Outcome kodu, ilgili piyasa için çözümlenemedi."""


def describe_atom(index: int) -> dict[str, str]:
    """Atom indeksini insan okunur skor bilgisine çevirir."""
    ht_home, ht_away, ft_home, ft_away = ATOMS[index]
    return {"half_time": f"{ht_home}-{ht_away}", "full_time": f"{ft_home}-{ft_away}"}


# --------------------------------------------------------------------------- #
# Outcome kodu ayrıştırıcıları
# --------------------------------------------------------------------------- #

_UNDER = {"ALT", "UNDER", "U"}
_OVER = {"UST", "ÜST", "OVER", "O"}
_TOTAL_RE = re.compile(r"^(?P<a>[^\d]*)(?P<line>\d+(?:[.,]\d+)?)(?P<b>[^\d]*)$")
_SCORE_RE = re.compile(r"^(\d+)\s*[-:]\s*(\d+)$")
_RANGE_RE = re.compile(r"^(\d+)\s*(?:-\s*(\d+)|\+)$")
_HANDICAP_RE = re.compile(r"^(?P<side>[12X])\s*\(?\s*(?P<hcap>[+-]?\d+(?:[.,]\d+)?)\s*\)?$", re.I)


def _norm(outcome: str) -> str:
    return outcome.strip().upper().replace("İ", "I")


def _parse_total(outcome: str) -> tuple[str, float]:
    """'2.5 Üst', 'Alt 2,5', 'OVER 2.5' -> ('OVER'|'UNDER', 2.5)"""
    match = _TOTAL_RE.match(_norm(outcome))
    if not match:
        raise UnknownOutcome(f"Alt/Üst outcome'u çözümlenemedi: {outcome!r}")
    words = f"{match.group('a')}{match.group('b')}".strip(" ()")
    line = float(match.group("line").replace(",", "."))
    if words in _OVER:
        return "OVER", line
    if words in _UNDER:
        return "UNDER", line
    raise UnknownOutcome(f"Alt/Üst yönü anlaşılamadı: {outcome!r}")


# --------------------------------------------------------------------------- #
# Piyasa tanımları
# --------------------------------------------------------------------------- #


def _match_result(ht: bool) -> Callable[[str], Callable[[Atom], bool]]:
    def build(outcome: str) -> Callable[[Atom], bool]:
        code = _norm(outcome)
        home, away = (0, 1) if ht else (2, 3)
        table = {
            "1": lambda a: a[home] > a[away],
            "X": lambda a: a[home] == a[away],
            "2": lambda a: a[home] < a[away],
        }
        if code not in table:
            raise UnknownOutcome(f"Maç sonucu outcome'u geçersiz: {outcome!r} (1, X, 2 bekleniyor)")
        return table[code]

    return build


def _double_chance(ht: bool) -> Callable[[str], Callable[[Atom], bool]]:
    def build(outcome: str) -> Callable[[Atom], bool]:
        code = "".join(sorted(_norm(outcome).replace(" ", "")))
        home, away = (0, 1) if ht else (2, 3)
        table = {
            "1X": lambda a: a[home] >= a[away],
            "12": lambda a: a[home] != a[away],
            "2X": lambda a: a[home] <= a[away],
        }
        if code not in table:
            raise UnknownOutcome(
                f"Çift şans outcome'u geçersiz: {outcome!r} (1X, 12, X2 bekleniyor)"
            )
        return table[code]

    return build


def _total_goals(ht: bool) -> Callable[[str], Callable[[Atom], bool]]:
    def build(outcome: str) -> Callable[[Atom], bool]:
        side, line = _parse_total(outcome)
        home, away = (0, 1) if ht else (2, 3)
        if side == "OVER":
            return lambda a: a[home] + a[away] > line
        return lambda a: a[home] + a[away] < line

    return build


def _both_teams_to_score(outcome: str) -> Callable[[Atom], bool]:
    code = _norm(outcome).replace(" ", "")
    if code in {"VAR", "YES", "EVET", "KGVAR"}:
        return lambda a: a[2] > 0 and a[3] > 0
    if code in {"YOK", "NO", "HAYIR", "KGYOK"}:
        return lambda a: a[2] == 0 or a[3] == 0
    raise UnknownOutcome(f"Karşılıklı gol outcome'u geçersiz: {outcome!r} (Var/Yok bekleniyor)")


def _correct_score(outcome: str) -> Callable[[Atom], bool]:
    match = _SCORE_RE.match(_norm(outcome))
    if not match:
        raise UnknownOutcome(f"Doğru skor outcome'u geçersiz: {outcome!r} ('2-1' bekleniyor)")
    home, away = int(match.group(1)), int(match.group(2))
    return lambda a: a[2] == home and a[3] == away


def _half_time_full_time(outcome: str) -> Callable[[Atom], bool]:
    parts = _norm(outcome).replace(" ", "").split("/")
    if len(parts) != 2:
        raise UnknownOutcome(f"İY/MS outcome'u geçersiz: {outcome!r} ('1/X' bekleniyor)")
    first = _match_result(ht=True)(parts[0])
    second = _match_result(ht=False)(parts[1])
    return lambda a: first(a) and second(a)


def _odd_even(outcome: str) -> Callable[[Atom], bool]:
    code = _norm(outcome).replace(" ", "")
    if code in {"TEK", "ODD"}:
        return lambda a: (a[2] + a[3]) % 2 == 1
    if code in {"CIFT", "ÇIFT", "EVEN"}:
        return lambda a: (a[2] + a[3]) % 2 == 0
    raise UnknownOutcome(f"Tek/Çift outcome'u geçersiz: {outcome!r}")


def _goal_range(outcome: str) -> Callable[[Atom], bool]:
    match = _RANGE_RE.match(_norm(outcome).replace(" ", ""))
    if not match:
        raise UnknownOutcome(f"Gol aralığı outcome'u geçersiz: {outcome!r} ('0-1' veya '6+')")
    low = int(match.group(1))
    high = int(match.group(2)) if match.group(2) else None
    if high is None:
        return lambda a: a[2] + a[3] >= low
    return lambda a: low <= a[2] + a[3] <= high


def _handicap(outcome: str) -> Callable[[Atom], bool]:
    match = _HANDICAP_RE.match(_norm(outcome))
    if not match:
        raise UnknownOutcome(f"Handikap outcome'u geçersiz: {outcome!r} ('1(-1)' bekleniyor)")
    side = match.group("side").upper()
    hcap = float(match.group("hcap").replace(",", "."))
    if side == "1":
        return lambda a: a[2] + hcap > a[3]
    if side == "2":
        return lambda a: a[3] + hcap > a[2]
    return lambda a: a[2] + hcap == a[3]


@dataclass(frozen=True)
class MarketDef:
    """Bir bahis piyasasının anlamı."""

    id: str
    label: str
    build: Callable[[str], Callable[[Atom], bool]]
    example_outcomes: tuple[str, ...]
    group: str = GOALS_GROUP


MARKETS: dict[str, MarketDef] = {
    m.id: m
    for m in (
        MarketDef("MS_1X2", "Maç Sonucu (1X2)", _match_result(ht=False), ("1", "X", "2")),
        MarketDef("CIFT_SANS", "Çift Şans", _double_chance(ht=False), ("1X", "12", "X2")),
        MarketDef("IY_1X2", "İlk Yarı Sonucu (1X2)", _match_result(ht=True), ("1", "X", "2")),
        MarketDef("IY_CIFT_SANS", "İlk Yarı Çift Şans", _double_chance(ht=True), ("1X", "X2")),
        MarketDef("ALT_UST", "Alt / Üst", _total_goals(ht=False), ("2.5 Alt", "2.5 Üst")),
        MarketDef("IY_ALT_UST", "İlk Yarı Alt / Üst", _total_goals(ht=True), ("0.5 Üst",)),
        MarketDef("KARSILIKLI_GOL", "Karşılıklı Gol", _both_teams_to_score, ("Var", "Yok")),
        MarketDef("DOGRU_SKOR", "Doğru Skor", _correct_score, ("1-0", "2-1")),
        MarketDef("IY_MS", "İlk Yarı / Maç Sonucu", _half_time_full_time, ("1/1", "X/2")),
        MarketDef("TEK_CIFT", "Toplam Gol Tek / Çift", _odd_even, ("Tek", "Çift")),
        MarketDef("TOPLAM_GOL", "Toplam Gol Aralığı", _goal_range, ("0-1", "2-3", "6+")),
        MarketDef("HANDIKAP", "Handikaplı Maç Sonucu", _handicap, ("1(-1)", "2(+1)")),
    )
}


@lru_cache(maxsize=4096)
def mask_for(market_id: str, outcome: str) -> int:
    """(piyasa, outcome) ikilisinin kazandığı atomların bit maskesi.

    Sonuç önbelleklenir; aynı outcome tekrar geldiğinde 1296 atom yeniden taranmaz.
    """
    market = MARKETS[market_id]
    predicate = market.build(outcome)
    mask = 0
    for index, atom in enumerate(ATOMS):
        if predicate(atom):
            mask |= 1 << index
    return mask
