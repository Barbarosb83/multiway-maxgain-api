"""Bahis piyasalarının anlamsal tanımları.

Bir seçimin (oddType + outcome + specialBetValue) *ne zaman kazandığını*
bilmeden iki seçimin birlikte tutup tutamayacağı söylenemez. Bu modül her
piyasayı, maçın somut sonuçları üzerinde bir yüklem olarak tanımlar.

Sonuç uzayı
-----------
Tüm piyasalar ortak bir skor uzayında yaşar::

    Atom = (İY_ev, İY_dep, MS_ev, MS_dep)

İlk yarı ve maç sonu skorlarının ``İY <= MS`` kısıtıyla birlikte tutulması,
"İY 2-0" ile "MS 1.5 Alt" gibi çelişkilerin kendiliğinden elenmesini sağlar.

Uzay **maça göre uyarlanır**. Skor tavanı sabit değildir; o maçtaki seçimlerin
eşiklerinden türetilir. Futbolda 2.5 gol sınırı için 4'lük bir tavan yeter,
basketbolda 220.5 sayı sınırı için ~222 gerekir. Uzay şu biçimlerde kurulur:

``JOINT``
    Dört boyutlu, ilk yarı dahil. Yalnızca tavan küçükken (futbol, hokey,
    hentbol) atom sayısı makul kalır.
``FLAT``
    İki boyutlu, atomlar ``(0, 0, ev, dep)``. Devre piyasası yoksa ya da grup
    tek bir periyoda aitse kullanılır; büyük tavanlara izin verir.
``HALF``
    İki boyutlu, atomlar ``(ev, dep, ev, dep)``. Yalnızca ilk yarı piyasalarını
    içeren gruplar için.

Ortak uzay atom sınırını aşarsa hesap periyotlara bölünür (bkz. ``max_gain``);
bu, periyotlar arası çelişki tespitini kaybettirir ama sonucu asla düşürmez.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache

__all__ = [
    "Atom",
    "Space",
    "SpaceKind",
    "MAX_ATOMS",
    "MIN_SCORE_BOUND",
    "MAX_SCORE_BOUND",
    "MarketDef",
    "MARKETS",
    "UnknownOutcome",
    "get_space",
    "mask_for",
    "required_bound",
    "describe_atom",
]

# (İY_ev, İY_dep, MS_ev, MS_dep)
Atom = tuple[int, int, int, int]

SpaceKind = str  # "JOINT" | "FLAT" | "HALF"

# Tek bir uzayda tutulacak azami atom sayısı. Maske kurulumu atom başına bir
# yüklem çağrısıdır; 65k atom ~20 ms sürer ve sonuç önbelleklenir.
MAX_ATOMS = 65_000
MIN_SCORE_BOUND = 4
MAX_SCORE_BOUND = 254  # FLAT uzayda (254+1)^2 = 65_025


class UnknownOutcome(ValueError):
    """Outcome / specialBetValue ikilisi, ilgili piyasa için çözümlenemedi."""


# --------------------------------------------------------------------------- #
# Sonuç uzayı
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Space:
    kind: SpaceKind
    max_score: int
    atoms: tuple[Atom, ...]

    @property
    def full_mask(self) -> int:
        return (1 << len(self.atoms)) - 1

    @property
    def key(self) -> tuple[str, int]:
        return (self.kind, self.max_score)


def _joint_atoms(bound: int) -> tuple[Atom, ...]:
    return tuple(
        (ht_home, ht_away, ft_home, ft_away)
        for ft_home in range(bound + 1)
        for ft_away in range(bound + 1)
        for ht_home in range(ft_home + 1)
        for ht_away in range(ft_away + 1)
    )


def _flat_atoms(bound: int) -> tuple[Atom, ...]:
    return tuple((0, 0, home, away) for home in range(bound + 1) for away in range(bound + 1))


def _half_atoms(bound: int) -> tuple[Atom, ...]:
    return tuple((home, away, home, away) for home in range(bound + 1) for away in range(bound + 1))


_BUILDERS: dict[SpaceKind, Callable[[int], tuple[Atom, ...]]] = {
    "JOINT": _joint_atoms,
    "FLAT": _flat_atoms,
    "HALF": _half_atoms,
}


def joint_fits(bound: int) -> bool:
    """Dört boyutlu uzay, verilen tavanla atom sınırına sığıyor mu?"""
    per_side = (bound + 1) * (bound + 2) // 2
    return per_side * per_side <= MAX_ATOMS


@lru_cache(maxsize=64)
def get_space(kind: SpaceKind, max_score: int) -> Space:
    return Space(kind=kind, max_score=max_score, atoms=_BUILDERS[kind](max_score))


def describe_atom(space: Space, index: int) -> dict[str, str]:
    """Atom indeksini insan okunur skora çevirir."""
    ht_home, ht_away, ft_home, ft_away = space.atoms[index]
    if space.kind == "HALF":
        return {"half_time": f"{ht_home}-{ht_away}"}
    if space.kind == "FLAT":
        return {"full_time": f"{ft_home}-{ft_away}"}
    return {"half_time": f"{ht_home}-{ht_away}", "full_time": f"{ft_home}-{ft_away}"}


# --------------------------------------------------------------------------- #
# Outcome / specialBetValue ayrıştırıcıları
# --------------------------------------------------------------------------- #

_UNDER = {"ALT", "UNDER", "U", "-"}
_OVER = {"UST", "ÜST", "OVER", "O", "+"}
_NUM_RE = re.compile(r"^[+-]?\d+(?:[.,]\d+)?$")
_PAIR_RE = re.compile(r"^([+-]?\d+(?:[.,]\d+)?)\s*[:/]\s*([+-]?\d+(?:[.,]\d+)?)$")
_SCORE_RE = re.compile(r"^(\d+)\s*[-:]\s*(\d+)$")
_RANGE_RE = re.compile(r"^(\d+)\s*(?:-\s*(\d+)|\+)$")


def _norm(value: str | None) -> str:
    return (value or "").strip().upper().replace("İ", "I")


def _number(text: str) -> float:
    return float(text.replace(",", "."))


def _line_from(outcome: str, special: str | None, label: str) -> tuple[str, float]:
    """Alt/Üst yönü ve eşiği çözer.

    Eşik öncelikle ``specialBetValue``'dan alınır; yoksa outcome'un içinden
    ayrıştırılmaya çalışılır (``"2.5 Üst"`` gibi birleşik kodlar için).
    """
    code = _norm(outcome).replace(" ", "")
    special_code = _norm(special)

    if special_code and _NUM_RE.match(special_code):
        line = _number(special_code)
        side = code.rstrip("0123456789.,+-") or code
        if side in _OVER:
            return "OVER", line
        if side in _UNDER:
            return "UNDER", line
        raise UnknownOutcome(f"{label} yönü anlaşılamadı: outcome={outcome!r}")

    match = re.match(r"^(?P<a>[^\d]*)(?P<line>\d+(?:[.,]\d+)?)(?P<b>[^\d]*)$", code)
    if not match:
        raise UnknownOutcome(
            f"{label} eşiği bulunamadı: outcome={outcome!r}, specialBetValue={special!r}"
        )
    words = f"{match.group('a')}{match.group('b')}".strip("()")
    line = _number(match.group("line"))
    if words in _OVER:
        return "OVER", line
    if words in _UNDER:
        return "UNDER", line
    raise UnknownOutcome(f"{label} yönü anlaşılamadı: outcome={outcome!r}")


def _handicap_from(special: str | None) -> tuple[float, float]:
    """``"0:1"`` -> (0, 1); ``"-1.5"`` -> (-1.5, 0)."""
    code = _norm(special).replace(" ", "")
    pair = _PAIR_RE.match(code)
    if pair:
        return _number(pair.group(1)), _number(pair.group(2))
    if _NUM_RE.match(code):
        return _number(code), 0.0
    raise UnknownOutcome(f"Handikap değeri çözümlenemedi: specialBetValue={special!r}")


# --------------------------------------------------------------------------- #
# Periyot seçiciler
# --------------------------------------------------------------------------- #

_PERIODS: dict[str, Callable[[Atom], tuple[int, int]]] = {
    "FT": lambda a: (a[2], a[3]),
    "HT": lambda a: (a[0], a[1]),
    "2H": lambda a: (a[2] - a[0], a[3] - a[1]),
}

Predicate = Callable[[Atom], bool]
Builder = Callable[[str, str | None], Predicate]
Bounder = Callable[[str, str | None], int]


def _flat_bound(_outcome: str, _special: str | None) -> int:
    return MIN_SCORE_BOUND


# --------------------------------------------------------------------------- #
# Piyasa yüklemleri
# --------------------------------------------------------------------------- #


def _match_result(period: str) -> Builder:
    score = _PERIODS[period]

    def build(outcome: str, _special: str | None) -> Predicate:
        table: dict[str, Predicate] = {
            "1": lambda a: score(a)[0] > score(a)[1],
            "X": lambda a: score(a)[0] == score(a)[1],
            "2": lambda a: score(a)[0] < score(a)[1],
        }
        code = _norm(outcome)
        if code not in table:
            raise UnknownOutcome(f"Maç sonucu outcome'u geçersiz: {outcome!r} (1, X, 2 bekleniyor)")
        return table[code]

    return build


def _double_chance(period: str) -> Builder:
    score = _PERIODS[period]

    def build(outcome: str, _special: str | None) -> Predicate:
        table: dict[str, Predicate] = {
            "1X": lambda a: score(a)[0] >= score(a)[1],
            "12": lambda a: score(a)[0] != score(a)[1],
            "2X": lambda a: score(a)[0] <= score(a)[1],
        }
        code = "".join(sorted(_norm(outcome).replace(" ", "").replace("-", "")))
        if code not in table:
            raise UnknownOutcome(f"Çift şans outcome'u geçersiz: {outcome!r} (1X, 12, X2)")
        return table[code]

    return build


def _draw_no_bet(period: str) -> Builder:
    """Beraberlikte iade; kazanç senaryosu açısından yalnızca 1 ve 2 tutar."""
    score = _PERIODS[period]

    def build(outcome: str, _special: str | None) -> Predicate:
        table: dict[str, Predicate] = {
            "1": lambda a: score(a)[0] > score(a)[1],
            "2": lambda a: score(a)[0] < score(a)[1],
        }
        code = _norm(outcome)
        if code not in table:
            raise UnknownOutcome(f"Draw No Bet outcome'u geçersiz: {outcome!r} (1, 2)")
        return table[code]

    return build


def _total(period: str, side: str = "both") -> tuple[Builder, Bounder]:
    """Alt/Üst. ``side``: 'both' | 'home' | 'away'."""
    score = _PERIODS[period]

    def value(atom: Atom) -> int:
        home, away = score(atom)
        if side == "home":
            return home
        if side == "away":
            return away
        return home + away

    def build(outcome: str, special: str | None) -> Predicate:
        direction, line = _line_from(outcome, special, "Alt/Üst")
        if direction == "OVER":
            return lambda a: value(a) > line
        return lambda a: value(a) < line

    def bound(outcome: str, special: str | None) -> int:
        _direction, line = _line_from(outcome, special, "Alt/Üst")
        return math.ceil(line) + 1

    return build, bound


def _total_3way(period: str) -> tuple[Builder, Bounder]:
    """Alt / Tam / Üst. 'X' toplamın eşiğe tam eşit olması demektir."""
    score = _PERIODS[period]

    def build(outcome: str, special: str | None) -> Predicate:
        if _norm(outcome).replace(" ", "") == "X":
            _direction, line = _line_from("Over", special, "Alt/Üst")
            return lambda a: sum(score(a)) == line
        direction, line = _line_from(outcome, special, "Alt/Üst")
        if direction == "OVER":
            return lambda a: sum(score(a)) > line
        return lambda a: sum(score(a)) < line

    def bound(outcome: str, special: str | None) -> int:
        _direction, line = _line_from("Over", special, "Alt/Üst")
        return math.ceil(line) + 1

    return build, bound


def _handicap(period: str) -> tuple[Builder, Bounder]:
    score = _PERIODS[period]

    def build(outcome: str, special: str | None) -> Predicate:
        home_hcap, away_hcap = _handicap_from(special)
        code = _norm(outcome)
        table: dict[str, Predicate] = {
            "1": lambda a: score(a)[0] + home_hcap > score(a)[1] + away_hcap,
            "X": lambda a: score(a)[0] + home_hcap == score(a)[1] + away_hcap,
            "2": lambda a: score(a)[0] + home_hcap < score(a)[1] + away_hcap,
        }
        if code not in table:
            raise UnknownOutcome(f"Handikap outcome'u geçersiz: {outcome!r} (1, X, 2)")
        return table[code]

    def bound(_outcome: str, special: str | None) -> int:
        home_hcap, away_hcap = _handicap_from(special)
        return math.ceil(max(abs(home_hcap), abs(away_hcap))) + MIN_SCORE_BOUND

    return build, bound


_COUNT_RE = re.compile(r"^(?P<low>\d+)\s*(?:-\s*(?P<high>\d+)|(?P<plus>\+))?$")


def _parse_count(outcome: str, special: str | None) -> tuple[int, int | None]:
    """'0' -> (0,0); '3+' -> (3,None); '0-1 goals' -> (0,1).

    Sondaki birim sözcüğü (goals, sets, points ...) atılır.
    """
    source = _norm(outcome) or _norm(special)
    source = re.sub(r"[A-ZÇĞİÖŞÜ]+\s*$", "", source).strip()
    match = _COUNT_RE.match(source.replace(" ", ""))
    if not match:
        raise UnknownOutcome(f"Sayı outcome'u çözümlenemedi: {outcome!r} ('0', '3+', '0-1')")
    low = int(match.group("low"))
    if match.group("plus"):
        return low, None
    if match.group("high"):
        return low, int(match.group("high"))
    return low, low


def _count(period: str, side: str = "both") -> tuple[Builder, Bounder]:
    """Tam sayı / aralık piyasası -- Over/Under değil, doğrudan adet."""
    score = _PERIODS[period]

    def value(atom: Atom) -> int:
        home, away = score(atom)
        if side == "home":
            return home
        if side == "away":
            return away
        return home + away

    def build(outcome: str, special: str | None) -> Predicate:
        low, high = _parse_count(outcome, special)
        if high is None:
            return lambda a: value(a) >= low
        return lambda a: low <= value(a) <= high

    def bound(outcome: str, special: str | None) -> int:
        low, high = _parse_count(outcome, special)
        return (high if high is not None else low) + 1

    return build, bound


def _both_teams_to_score(outcome: str, _special: str | None) -> Predicate:
    code = _norm(outcome).replace(" ", "")
    if code in {"VAR", "YES", "EVET", "KGVAR", "GOAL", "1"}:
        return lambda a: a[2] > 0 and a[3] > 0
    if code in {"YOK", "NO", "HAYIR", "KGYOK", "NOGOAL", "2"}:
        return lambda a: a[2] == 0 or a[3] == 0
    raise UnknownOutcome(f"Karşılıklı gol outcome'u geçersiz: {outcome!r} (Var/Yok)")


def _correct_score(outcome: str, special: str | None) -> Predicate:
    # "Others" / "other" / "C" toplayıcı kodlardır: tek bir skora karşılık
    # gelmedikleri için modellenemez ve seçim yalıtılır.
    match = _SCORE_RE.match(_norm(outcome)) or _SCORE_RE.match(_norm(special))
    if not match:
        raise UnknownOutcome(f"Doğru skor outcome'u geçersiz: {outcome!r} ('2-1' bekleniyor)")
    home, away = int(match.group(1)), int(match.group(2))
    return lambda a: a[2] == home and a[3] == away


def _correct_score_bound(outcome: str, special: str | None) -> int:
    match = _SCORE_RE.match(_norm(outcome)) or _SCORE_RE.match(_norm(special))
    if not match:
        raise UnknownOutcome(f"Doğru skor outcome'u geçersiz: {outcome!r}")
    return max(int(match.group(1)), int(match.group(2))) + 1


_HTFT_LETTERS = {"H": "1", "D": "X", "A": "2"}


def _half_time_full_time(outcome: str, _special: str | None) -> Predicate:
    """'1/X', '1X' ve 'HD' kodlamalarını kabul eder (sağlayıcı üçünü de kullanıyor)."""
    code = _norm(outcome).replace(" ", "")
    if "/" in code:
        parts = code.split("/")
    elif len(code) == 2:
        parts = [_HTFT_LETTERS.get(char, char) for char in code]
    else:
        parts = [code]
    if len(parts) != 2:
        raise UnknownOutcome(f"İY/MS outcome'u geçersiz: {outcome!r} ('1/X' bekleniyor)")
    first = _match_result("HT")(parts[0], None)
    second = _match_result("FT")(parts[1], None)
    return lambda a: first(a) and second(a)


def _odd_even(period: str) -> Builder:
    score = _PERIODS[period]

    def build(outcome: str, _special: str | None) -> Predicate:
        code = _norm(outcome).replace(" ", "")
        if code in {"TEK", "ODD"}:
            return lambda a: sum(score(a)) % 2 == 1
        if code in {"CIFT", "ÇIFT", "EVEN"}:
            return lambda a: sum(score(a)) % 2 == 0
        raise UnknownOutcome(f"Tek/Çift outcome'u geçersiz: {outcome!r}")

    return build


def _goal_range(outcome: str, special: str | None) -> Predicate:
    source = _norm(outcome).replace(" ", "") or _norm(special).replace(" ", "")
    match = _RANGE_RE.match(source)
    if not match:
        raise UnknownOutcome(f"Gol aralığı outcome'u geçersiz: {outcome!r} ('0-1' veya '6+')")
    low = int(match.group(1))
    high = int(match.group(2)) if match.group(2) else None
    if high is None:
        return lambda a: a[2] + a[3] >= low
    return lambda a: low <= a[2] + a[3] <= high


def _goal_range_bound(outcome: str, special: str | None) -> int:
    source = _norm(outcome).replace(" ", "") or _norm(special).replace(" ", "")
    match = _RANGE_RE.match(source)
    if not match:
        raise UnknownOutcome(f"Gol aralığı outcome'u geçersiz: {outcome!r}")
    high = int(match.group(2)) if match.group(2) else int(match.group(1))
    return high + 1


# --------------------------------------------------------------------------- #
# Piyasa kaydı
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class MarketDef:
    """Bir bahis piyasasının anlamı."""

    id: str
    label: str
    period: str  # "FT" | "HT" | "2H" -- periyot bölmesi için
    build: Builder
    bound: Bounder
    example_outcomes: tuple[str, ...]
    needs_special: bool = False


def _market(
    market_id: str,
    label: str,
    period: str,
    builder: Builder | tuple[Builder, Bounder],
    examples: tuple[str, ...],
    *,
    bounder: Bounder | None = None,
    needs_special: bool = False,
) -> MarketDef:
    if isinstance(builder, tuple):
        builder, bounder = builder
    return MarketDef(
        id=market_id,
        label=label,
        period=period,
        build=builder,
        bound=bounder or _flat_bound,
        example_outcomes=examples,
        needs_special=needs_special,
    )


MARKETS: dict[str, MarketDef] = {
    m.id: m
    for m in (
        # Sonuç ailesi -- sporlar arasında yapı olarak aynıdır
        _market("MS_1X2", "Maç Sonucu (1X2)", "FT", _match_result("FT"), ("1", "X", "2")),
        _market("CIFT_SANS", "Çift Şans", "FT", _double_chance("FT"), ("1X", "12", "X2")),
        _market("DNB", "Draw No Bet", "FT", _draw_no_bet("FT"), ("1", "2")),
        _market("IY_1X2", "İlk Yarı Sonucu", "HT", _match_result("HT"), ("1", "X", "2")),
        _market("IY_CIFT_SANS", "İlk Yarı Çift Şans", "HT", _double_chance("HT"), ("1X", "X2")),
        _market("IY_DNB", "İlk Yarı Draw No Bet", "HT", _draw_no_bet("HT"), ("1", "2")),
        _market("IY2_1X2", "İkinci Yarı Sonucu", "2H", _match_result("2H"), ("1", "X", "2")),
        _market("IY2_CIFT_SANS", "İkinci Yarı Çift Şans", "2H", _double_chance("2H"), ("1X", "X2")),
        _market("IY2_DNB", "İkinci Yarı Draw No Bet", "2H", _draw_no_bet("2H"), ("1", "2")),
        # Alt / Üst -- eşik specialBetValue'dan gelir
        _market("ALT_UST", "Alt / Üst", "FT", _total("FT"), ("Alt", "Üst"), needs_special=True),
        _market(
            "ALT_UST_EV", "Alt / Üst (Ev)", "FT", _total("FT", "home"), ("Alt",), needs_special=True
        ),
        _market(
            "ALT_UST_DEP",
            "Alt / Üst (Deplasman)",
            "FT",
            _total("FT", "away"),
            ("Üst",),
            needs_special=True,
        ),
        _market(
            "IY_ALT_UST", "İlk Yarı Alt / Üst", "HT", _total("HT"), ("Üst",), needs_special=True
        ),
        _market(
            "IY2_ALT_UST", "İkinci Yarı Alt / Üst", "2H", _total("2H"), ("Alt",), needs_special=True
        ),
        # Handikap -- specialBetValue "0:1" ya da "-1.5"
        _market(
            "ALT_UST_3WAY",
            "Alt / Tam / Üst",
            "FT",
            _total_3way("FT"),
            ("Over", "X", "Under"),
            needs_special=True,
        ),
        _market("HANDIKAP", "Handikap", "FT", _handicap("FT"), ("1", "X", "2"), needs_special=True),
        _market(
            "IY_HANDIKAP",
            "İlk Yarı Handikap",
            "HT",
            _handicap("HT"),
            ("1", "2"),
            needs_special=True,
        ),
        # Gol bazlı
        _market("KARSILIKLI_GOL", "Karşılıklı Gol", "FT", _both_teams_to_score, ("Var", "Yok")),
        _market(
            "DOGRU_SKOR",
            "Doğru Skor",
            "FT",
            _correct_score,
            ("1-0", "2-1"),
            bounder=_correct_score_bound,
        ),
        _market("IY_MS", "İlk Yarı / Maç Sonucu", "FT", _half_time_full_time, ("1/1", "X/2")),
        _market("TEK_CIFT", "Tek / Çift", "FT", _odd_even("FT"), ("Tek", "Çift")),
        _market("IY_TEK_CIFT", "İlk Yarı Tek / Çift", "HT", _odd_even("HT"), ("Tek", "Çift")),
        # Adet / aralık piyasaları -- Over/Under değil, doğrudan sayı
        _market("GOL_SAYISI", "Gol Sayısı", "FT", _count("FT"), ("0", "2-3", "6+")),
        _market("IY_GOL_SAYISI", "İlk Yarı Gol Sayısı", "HT", _count("HT"), ("0", "1", "2+")),
        _market("IY2_GOL_SAYISI", "İkinci Yarı Gol Sayısı", "2H", _count("2H"), ("0", "1", "2+")),
        _market("GOL_SAYISI_EV", "Ev Gol Sayısı", "FT", _count("FT", "home"), ("0", "3+")),
        _market("GOL_SAYISI_DEP", "Deplasman Gol Sayısı", "FT", _count("FT", "away"), ("0", "3+")),
        _market(
            "IY_GOL_SAYISI_EV", "İlk Yarı Ev Gol Sayısı", "HT", _count("HT", "home"), ("0", "3+")
        ),
        _market(
            "IY_GOL_SAYISI_DEP",
            "İlk Yarı Deplasman Gol Sayısı",
            "HT",
            _count("HT", "away"),
            ("0", "3+"),
        ),
        _market(
            "TOPLAM_GOL",
            "Toplam Gol Aralığı",
            "FT",
            _goal_range,
            ("0-1", "2-3", "6+"),
            bounder=_goal_range_bound,
        ),
    )
}


# --------------------------------------------------------------------------- #
# Maske üretimi
# --------------------------------------------------------------------------- #


def required_bound(
    market_id: str, outcome: str, special: str | None, siblings: tuple[str, ...] = ()
) -> int:
    """Seçimin doğru değerlendirilebilmesi için gereken skor tavanı.

    Toplayıcı outcome'lar ("Others") kardeşlerinin tümleyenidir; dolayısıyla
    kardeşlerin tamamını kapsayacak bir tavan gerekir.
    """
    market = MARKETS[market_id]
    if is_aggregate(outcome):
        bounds = [
            market.bound(sibling, special) for sibling in siblings if not is_aggregate(sibling)
        ]
        needed = max(bounds) if bounds else MIN_SCORE_BOUND
    else:
        needed = market.bound(outcome, special)
    return max(MIN_SCORE_BOUND, min(needed, MAX_SCORE_BOUND))


AGGREGATE_OUTCOMES = {"OTHERS", "OTHER", "C"}


def is_aggregate(outcome: str) -> bool:
    """'Others' / 'other' / 'C' gibi toplayıcı kodlar tek bir sonuca karşılık gelmez."""
    return _norm(outcome).replace(" ", "") in AGGREGATE_OUTCOMES


@lru_cache(maxsize=8192)
def mask_for(
    market_id: str,
    outcome: str,
    special: str | None,
    space_key: tuple[str, int],
    siblings: tuple[str, ...] = (),
) -> int:
    """(piyasa, outcome, specialBetValue) ikilisinin kazandığı atomların bit maskesi.

    Maske bytearray üzerinden kurulur: her atom için tek bir bit yazılır ve
    sonuç tek seferde tamsayıya çevrilir. Doğrudan ``mask |= 1 << i`` yaklaşımı
    her adımda maskenin tamamını kopyaladığı için atom sayısı büyüdükçe
    karesel yavaşlar.
    """
    space = get_space(*space_key)

    if is_aggregate(outcome):
        # "Others" = listelenen diğer sonuçların hiçbiri. Kardeş outcome'ların
        # birleşiminin tümleyeni tam olarak bunu verir.
        covered = 0
        for sibling in siblings:
            if not is_aggregate(sibling):
                covered |= mask_for(market_id, sibling, special, space_key)
        return space.full_mask & ~covered

    predicate = MARKETS[market_id].build(outcome, special)

    buffer = bytearray((len(space.atoms) + 7) // 8)
    for index, atom in enumerate(space.atoms):
        if predicate(atom):
            buffer[index >> 3] |= 1 << (index & 7)
    return int.from_bytes(buffer, "little")
