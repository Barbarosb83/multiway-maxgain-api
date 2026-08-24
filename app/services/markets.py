"""Bahis piyasalarının anlamsal tanımları.

Bir seçimin (oddType + outcome + specialBetValue) *ne zaman kazandığını*
bilmeden iki seçimin birlikte tutup tutamayacağı söylenemez. Bu modül her
piyasayı, maçın somut sonuçları üzerinde bir yüklem olarak tanımlar.

Sonuç uzayı
-----------
Uzay, maçın **parçalara** (segment) ayrılmış skorlarıdır. İki yerleşim vardır:

``MATCH``
    Tek parça, atom ``(ev, dep)``. Gruptaki bütün seçimler aynı periyoda
    aitse kullanılır -- maç sonu, ilk yarı, 2. çeyrek, 3. periyot fark etmez.
    İki boyutlu olduğu için basketbol/kriket gibi büyük skorlara da yeter.

``HALVES``
    İki parça, atom ``(İY_ev, İY_dep, 2Y_ev, 2Y_dep)``. Maç sonu bunların
    toplamıdır. İlk yarı ve maç sonu piyasaları birlikte geldiğinde kullanılır
    ve "İY 2-0" ile "MS 1.5 Alt" gibi çelişkileri yakalar.

Periyotlar karışıyor ama ortak yerleşim atom sınırına sığmıyorsa hesap
periyotlara bölünür (bkz. ``max_gain``); periyotlar arası çelişki tespiti
kaybolur ama sonuç asla düşmez.

Yüklemler atomun ham indekslerine değil, ``market.periods`` sırasına göre
verilen ``(ev, dep)`` ikililerine bakar. Bu sayede tek bir "maç sonucu"
tanımı maç sonu, ilk yarı, çeyrek ve periyot varyantlarında yeniden kullanılır.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from functools import lru_cache

__all__ = [
    "Atom",
    "Scores",
    "Space",
    "MAX_ATOMS",
    "MIN_SCORE_BOUND",
    "MAX_SCORE_BOUND",
    "HALF_PERIODS",
    "MarketDef",
    "MARKETS",
    "UnknownOutcome",
    "AGGREGATE_OUTCOMES",
    "get_space",
    "layout_fits",
    "mask_for",
    "feasible_mask",
    "parse_score",
    "required_bound",
    "describe_atom",
    "PERIOD_LABEL",
    "is_aggregate",
]

Atom = tuple[int, ...]
Scores = tuple[tuple[int, int], ...]
Predicate = Callable[[Scores], bool]
Builder = Callable[[str, str | None], Predicate]
Bounder = Callable[[str, str | None], int]

MAX_ATOMS = 65_000
MIN_SCORE_BOUND = 4
MAX_SCORE_BOUND = 254

# HALVES yerleşimiyle modellenebilen periyotlar. Diğerleri (çeyrek, periyot)
# yalnızca tek başlarına, MATCH yerleşiminde çözülür.
HALF_PERIODS = frozenset({"FT", "HT", "2H"})


class UnknownOutcome(ValueError):
    """Outcome / specialBetValue ikilisi, ilgili piyasa için çözümlenemedi."""


# --------------------------------------------------------------------------- #
# Sonuç uzayı
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Space:
    layout: str  # "MATCH" | "HALVES"
    max_score: int
    atoms: tuple[Atom, ...]

    @property
    def full_mask(self) -> int:
        return (1 << len(self.atoms)) - 1

    @property
    def key(self) -> tuple[str, int]:
        return (self.layout, self.max_score)


def _match_atoms(bound: int) -> tuple[Atom, ...]:
    return tuple((home, away) for home in range(bound + 1) for away in range(bound + 1))


def _halves_atoms(bound: int) -> tuple[Atom, ...]:
    return tuple(
        (first_home, first_away, second_home, second_away)
        for first_home in range(bound + 1)
        for first_away in range(bound + 1)
        for second_home in range(bound + 1)
        for second_away in range(bound + 1)
    )


_BUILDERS: dict[str, Callable[[int], tuple[Atom, ...]]] = {
    "MATCH": _match_atoms,
    "HALVES": _halves_atoms,
}


def layout_fits(layout: str, bound: int) -> bool:
    """Yerleşim, verilen tavanla atom sınırına sığıyor mu?"""
    dimensions = 2 if layout == "MATCH" else 4
    return (bound + 1) ** dimensions <= MAX_ATOMS


@lru_cache(maxsize=64)
def get_space(layout: str, max_score: int) -> Space:
    return Space(layout=layout, max_score=max_score, atoms=_BUILDERS[layout](max_score))


def _accessor(layout: str, period: str) -> Callable[[Atom], tuple[int, int]]:
    """Atomdan bir periyodun (ev, dep) skorunu okur."""
    if layout == "MATCH":
        return lambda a: (a[0], a[1])
    if period == "HT":
        return lambda a: (a[0], a[1])
    if period == "2H":
        return lambda a: (a[2], a[3])
    return lambda a: (a[0] + a[2], a[1] + a[3])  # FT = iki yarının toplamı


# Periyot kodu -> yanıtta görünecek anahtar.
PERIOD_LABEL = {
    "FT": "full_time",
    "HT": "half_time",
    "2H": "second_half",
    **{f"Q{i}": f"quarter_{i}" for i in range(1, 5)},
    **{f"P{i}": f"period_{i}" for i in range(1, 6)},
}


def describe_atom(space: Space, index: int, period: str = "FT") -> dict[str, str]:
    """Atom indeksini insan okunur skora çevirir.

    ``MATCH`` yerleşiminde yalnızca o grubun periyodu raporlanır; ``HALVES``
    yerleşiminde ilk yarı ve maç sonu birlikte verilir.
    """
    atom = space.atoms[index]
    if space.layout == "MATCH":
        return {PERIOD_LABEL.get(period, period): f"{atom[0]}-{atom[1]}"}
    return {
        "half_time": f"{atom[0]}-{atom[1]}",
        "full_time": f"{atom[0] + atom[2]}-{atom[1] + atom[3]}",
    }


# --------------------------------------------------------------------------- #
# Outcome ayrıştırıcıları
# --------------------------------------------------------------------------- #

# Sağlayıcı outcome adlarını sağlayıcı dilinde gönderebiliyor (Almanca "Über",
# Türkçe "Üst", İngilizce "Over"). Kanonik anlam aynı olduğu için hepsi kabul
# edilir.
_UNDER = {"ALT", "UNDER", "UNTER", "U"}
_OVER = {"UST", "ÜST", "OVER", "UBER", "ÜBER", "O"}
_NUM_RE = re.compile(r"^[+-]?\d+(?:[.,]\d+)?$")
_PAIR_RE = re.compile(r"^([+-]?\d+(?:[.,]\d+)?)\s*[:/]\s*([+-]?\d+(?:[.,]\d+)?)$")
_SCORE_RE = re.compile(r"^(\d+)\s*[-:]\s*(\d+)$")
_COUNT_RE = re.compile(r"^(?P<low>\d+)\s*(?:-\s*(?P<high>\d+)|(?P<plus>\+))?$")

AGGREGATE_OUTCOMES = {"OTHERS", "OTHER", "C"}


def _norm(value: str | None) -> str:
    return (value or "").strip().upper().replace("İ", "I")


def is_aggregate(outcome: str) -> bool:
    """'Others' / 'other' / 'C' gibi toplayıcı kodlar tek bir sonuca karşılık gelmez."""
    return _norm(outcome).replace(" ", "") in AGGREGATE_OUTCOMES


def _number(text: str) -> float:
    return float(text.replace(",", "."))


def _line_from(outcome: str, special: str | None, label: str = "Alt/Üst") -> tuple[str, float]:
    """Alt/Üst yönü ve eşiği. Eşik öncelikle ``specialBetValue``'dan alınır."""
    code = _norm(outcome).replace(" ", "")
    special_code = _norm(special)

    if special_code and _NUM_RE.match(special_code):
        side = code.rstrip("0123456789.,+-") or code
        if side in _OVER:
            return "OVER", _number(special_code)
        if side in _UNDER:
            return "UNDER", _number(special_code)
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


def _parse_count(outcome: str, special: str | None) -> tuple[int, int | None]:
    """'0' -> (0,0); '3+' -> (3,None); '0-1 goals' -> (0,1)."""
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


# --------------------------------------------------------------------------- #
# Tekil yüklemler -- hepsi scores[0] = (ev, dep) üzerinde çalışır
# --------------------------------------------------------------------------- #


def _result_predicate(code: str) -> Callable[[tuple[int, int]], bool]:
    table = {
        "1": lambda p: p[0] > p[1],
        "X": lambda p: p[0] == p[1],
        "2": lambda p: p[0] < p[1],
        "1X": lambda p: p[0] >= p[1],
        "12": lambda p: p[0] != p[1],
        "X2": lambda p: p[0] <= p[1],
    }
    if code not in table:
        raise UnknownOutcome(f"Sonuç kodu geçersiz: {code!r}")
    return table[code]


def _match_result(outcome: str, _special: str | None) -> Predicate:
    inner = _result_predicate(_norm(outcome))
    if _norm(outcome) not in ("1", "X", "2"):
        raise UnknownOutcome(f"Maç sonucu outcome'u geçersiz: {outcome!r} (1, X, 2 bekleniyor)")
    return lambda s: inner(s[0])


def _double_chance(outcome: str, _special: str | None) -> Predicate:
    code = "".join(sorted(_norm(outcome).replace(" ", "").replace("-", "")))
    table = {"1X": "1X", "12": "12", "2X": "X2"}
    if code not in table:
        raise UnknownOutcome(f"Çift şans outcome'u geçersiz: {outcome!r} (1X, 12, X2)")
    inner = _result_predicate(table[code])
    return lambda s: inner(s[0])


def _draw_no_bet(outcome: str, _special: str | None) -> Predicate:
    """Beraberlikte iade; kazanç senaryosu açısından yalnızca 1 ve 2 tutar."""
    code = _norm(outcome)
    if code not in ("1", "2"):
        raise UnknownOutcome(f"Draw No Bet outcome'u geçersiz: {outcome!r} (1, 2)")
    inner = _result_predicate(code)
    return lambda s: inner(s[0])


def _total(side: str = "both") -> tuple[Builder, Bounder]:
    def value(pair: tuple[int, int]) -> int:
        if side == "home":
            return pair[0]
        if side == "away":
            return pair[1]
        return pair[0] + pair[1]

    def build(outcome: str, special: str | None) -> Predicate:
        direction, line = _line_from(outcome, special)
        if direction == "OVER":
            return lambda s: value(s[0]) > line
        return lambda s: value(s[0]) < line

    def bound(outcome: str, special: str | None) -> int:
        _direction, line = _line_from(outcome, special)
        return math.ceil(line) + 1

    return build, bound


def _total_3way() -> tuple[Builder, Bounder]:
    """Alt / Tam / Üst. 'X' toplamın eşiğe tam eşit olması demektir."""

    def build(outcome: str, special: str | None) -> Predicate:
        if _norm(outcome).replace(" ", "") == "X":
            _direction, line = _line_from("Over", special)
            return lambda s: s[0][0] + s[0][1] == line
        direction, line = _line_from(outcome, special)
        if direction == "OVER":
            return lambda s: s[0][0] + s[0][1] > line
        return lambda s: s[0][0] + s[0][1] < line

    def bound(_outcome: str, special: str | None) -> int:
        _direction, line = _line_from("Over", special)
        return math.ceil(line) + 1

    return build, bound


def _handicap() -> tuple[Builder, Bounder]:
    def build(outcome: str, special: str | None) -> Predicate:
        home_hcap, away_hcap = _handicap_from(special)
        code = _norm(outcome)
        table: dict[str, Predicate] = {
            "1": lambda s: s[0][0] + home_hcap > s[0][1] + away_hcap,
            "X": lambda s: s[0][0] + home_hcap == s[0][1] + away_hcap,
            "2": lambda s: s[0][0] + home_hcap < s[0][1] + away_hcap,
        }
        if code not in table:
            raise UnknownOutcome(f"Handikap outcome'u geçersiz: {outcome!r} (1, X, 2)")
        return table[code]

    def bound(_outcome: str, special: str | None) -> int:
        home_hcap, away_hcap = _handicap_from(special)
        return math.ceil(max(abs(home_hcap), abs(away_hcap))) + MIN_SCORE_BOUND

    return build, bound


# Evet/hayır tipi outcome'ların dile göre karşılıkları.
_AFFIRMATIVE = {"VAR", "YES", "Y", "EVET", "E", "KGVAR", "GOAL", "J", "JA", "SI", "OUI", "1"}
_NEGATIVE = {"YOK", "NO", "HAYIR", "H", "KGYOK", "NOGOAL", "N", "NEIN", "NON", "2"}


def parse_score(text: str | None) -> tuple[int, int] | None:
    """``"2:1"`` -> (2, 1). Çözümlenemezse None."""
    match = _SCORE_RE.match(_norm(text))
    return (int(match.group(1)), int(match.group(2))) if match else None


def _current_score(special: str | None) -> tuple[int, int]:
    """``"0:0"`` -> (0, 0). Canlı piyasalarda bahis anındaki skor."""
    score = parse_score(special)
    if score is None:
        raise UnknownOutcome(
            f"Anlık skor çözümlenemedi: specialBetValue={special!r} ('0:0' bekleniyor)"
        )
    return score


def _rest_result() -> tuple[Builder, Bounder]:
    """Maçın kalanını kim kazanır.

    Canlı piyasalarda bahis, o anki skordan sonrasına yatırılır: kazanan taraf
    *kalan sürede* daha çok gol atandır. ``specialBetValue`` bahis anındaki
    skoru taşır; yüklem maç sonu skorundan bu skoru düşerek çalışır ve maç
    sonunun anlık skordan küçük olamayacağını da kısıt olarak ekler.

    Anlık skor ``0:0`` olduğunda piyasa maç sonucuyla aynıya indirgenir.
    """

    def build(outcome: str, special: str | None) -> Predicate:
        current_home, current_away = _current_score(special)
        code = _norm(outcome)
        if code not in ("1", "X", "2"):
            raise UnknownOutcome(f"Kalan maç outcome'u geçersiz: {outcome!r} (1, X, 2)")
        inner = _result_predicate(code)

        def predicate(scores: Scores) -> bool:
            home, away = scores[0]
            if home < current_home or away < current_away:
                return False  # maç sonu, anlık skorun altına inemez
            return inner((home - current_home, away - current_away))

        return predicate

    def bound(_outcome: str, special: str | None) -> int:
        current_home, current_away = _current_score(special)
        return max(current_home, current_away) + MIN_SCORE_BOUND

    return build, bound


def _next_goal() -> tuple[Builder, Bounder]:
    """Sıradaki golü kim atar.

    Bu bir *sıralama* iddiasıdır; modellenen sonuç uzayı ise yalnızca skorları
    taşır, golün ne zaman atıldığını değil. Yüklem bu yüzden bahsin maç sonu
    skoruna izdüşümüdür: "deplasman sıradaki golü atar" bahsi, deplasmanın
    anlık skorun üstüne en az bir gol eklediği her sonuçta kazanabilir.

    İzdüşüm bir üst kümedir; yani tek başına bir sıralama piyasası varken
    kesindir (o skora götüren bir gol sırası daima kurulabilir), aynı maçta
    birden fazla sıralama piyasası varsa sonucu bir miktar yüksek tutabilir.
    Her hâlükârda piyasayı hiç modellememekten daha dardır: yalıtılmış bir
    seçim her zaman toplanırken, izdüşüm çelişenleri eleyebilir.
    """

    def build(outcome: str, special: str | None) -> Predicate:
        current_home, current_away = _current_score(special)
        code = _norm(outcome)
        if code == "1":
            return lambda s: s[0][0] >= current_home + 1 and s[0][1] >= current_away
        if code == "2":
            return lambda s: s[0][1] >= current_away + 1 and s[0][0] >= current_home
        if code == "X":
            # Daha gol atılmaz: maç anlık skorla biter.
            return lambda s: s[0] == (current_home, current_away)
        raise UnknownOutcome(f"Sıradaki gol outcome'u geçersiz: {outcome!r} (1, X, 2)")

    def bound(_outcome: str, special: str | None) -> int:
        current_home, current_away = _current_score(special)
        return max(current_home, current_away) + MIN_SCORE_BOUND

    return build, bound


def _btts_predicate(code: str) -> Callable[[tuple[int, int]], bool]:
    if code in _AFFIRMATIVE:
        return lambda p: p[0] > 0 and p[1] > 0
    if code in _NEGATIVE:
        return lambda p: p[0] == 0 or p[1] == 0
    raise UnknownOutcome(f"Karşılıklı gol outcome'u geçersiz: {code!r} (Var/Yok)")


def _both_teams_to_score(outcome: str, _special: str | None) -> Predicate:
    inner = _btts_predicate(_norm(outcome).replace(" ", ""))
    return lambda s: inner(s[0])


def _correct_score(outcome: str, special: str | None) -> Predicate:
    match = _SCORE_RE.match(_norm(outcome)) or _SCORE_RE.match(_norm(special))
    if not match:
        raise UnknownOutcome(f"Doğru skor outcome'u geçersiz: {outcome!r} ('2-1' bekleniyor)")
    home, away = int(match.group(1)), int(match.group(2))
    return lambda s: s[0] == (home, away)


def _correct_score_bound(outcome: str, special: str | None) -> int:
    match = _SCORE_RE.match(_norm(outcome)) or _SCORE_RE.match(_norm(special))
    if not match:
        raise UnknownOutcome(f"Doğru skor outcome'u geçersiz: {outcome!r}")
    return max(int(match.group(1)), int(match.group(2))) + 1


_HTFT_LETTERS = {"H": "1", "D": "X", "A": "2"}


def _half_time_full_time(outcome: str, _special: str | None) -> Predicate:
    """'1/X', '1X' ve 'HD' kodlamalarını kabul eder; scores = (İY, MS)."""
    code = _norm(outcome).replace(" ", "")
    if "/" in code:
        parts = code.split("/")
    elif len(code) == 2:
        parts = [_HTFT_LETTERS.get(char, char) for char in code]
    else:
        parts = [code]
    if len(parts) != 2 or any(part not in ("1", "X", "2") for part in parts):
        raise UnknownOutcome(f"İY/MS outcome'u geçersiz: {outcome!r} ('1/X' bekleniyor)")
    first = _result_predicate(parts[0])
    second = _result_predicate(parts[1])
    return lambda s: first(s[0]) and second(s[1])


def _odd_even(outcome: str, _special: str | None) -> Predicate:
    code = _norm(outcome).replace(" ", "")
    if code in {"TEK", "ODD", "UNGERADE"}:
        return lambda s: (s[0][0] + s[0][1]) % 2 == 1
    if code in {"CIFT", "ÇIFT", "EVEN", "GERADE"}:
        return lambda s: (s[0][0] + s[0][1]) % 2 == 0
    raise UnknownOutcome(f"Tek/Çift outcome'u geçersiz: {outcome!r}")


def _count(side: str = "both") -> tuple[Builder, Bounder]:
    """Tam sayı / aralık piyasası -- Over/Under değil, doğrudan adet."""

    def value(pair: tuple[int, int]) -> int:
        if side == "home":
            return pair[0]
        if side == "away":
            return pair[1]
        return pair[0] + pair[1]

    def build(outcome: str, special: str | None) -> Predicate:
        low, high = _parse_count(outcome, special)
        if high is None:
            return lambda s: value(s[0]) >= low
        return lambda s: low <= value(s[0]) <= high

    def bound(outcome: str, special: str | None) -> int:
        low, high = _parse_count(outcome, special)
        return (high if high is not None else low) + 1

    return build, bound


# --------------------------------------------------------------------------- #
# Kombine piyasalar -- "Over and home", "Home / Yes", "DrawAway / Under" ...
# --------------------------------------------------------------------------- #

_COMBO_SPLIT = re.compile(r"\s+AND\s+|\s*/\s*")
_COMBO_RESULT = {"HOME": "1", "AWAY": "2", "DRAW": "X", "1": "1", "2": "2", "X": "X"}
_COMBO_DC = {
    "HOMEDRAW": "1X",
    "HOMEAWAY": "12",
    "DRAWAWAY": "X2",
    "1X": "1X",
    "12": "12",
    "X2": "X2",
}
_COMBO_BTTS = _AFFIRMATIVE | _NEGATIVE
_COMBO_TOTAL = _OVER | _UNDER


def _combo_part(token: str, special: str | None) -> Callable[[tuple[int, int]], bool]:
    """Kombine outcome'un tek bir bileşenini yükleme çevirir."""
    code = token.strip().replace(" ", "")
    if code in _COMBO_RESULT:
        return _result_predicate(_COMBO_RESULT[code])
    if code in _COMBO_DC:
        return _result_predicate(_COMBO_DC[code])
    if code in _COMBO_BTTS:
        return _btts_predicate(code)
    if code in _COMBO_TOTAL:
        direction, line = _line_from(code, special)
        if direction == "OVER":
            return lambda p: p[0] + p[1] > line
        return lambda p: p[0] + p[1] < line
    raise UnknownOutcome(f"Kombine outcome bileşeni tanınmadı: {token!r}")


def _combo() -> tuple[Builder, Bounder]:
    """İki koşulun kesişimi. Bileşen sırası önemsizdir."""

    def parts_of(outcome: str) -> list[str]:
        tokens = [part for part in _COMBO_SPLIT.split(_norm(outcome)) if part.strip()]
        if len(tokens) < 2:
            raise UnknownOutcome(
                f"Kombine outcome iki bileşen içermeli: {outcome!r} ('Over and home')"
            )
        return tokens

    def build(outcome: str, special: str | None) -> Predicate:
        predicates = [_combo_part(token, special) for token in parts_of(outcome)]
        return lambda s: all(predicate(s[0]) for predicate in predicates)

    def bound(outcome: str, special: str | None) -> int:
        for token in parts_of(outcome):
            if token.strip().replace(" ", "") in _COMBO_TOTAL:
                _direction, line = _line_from(token, special)
                return math.ceil(line) + 1
        return MIN_SCORE_BOUND

    return build, bound


def _result_or_btts(result_code: str) -> Builder:
    """'Ev kazanır VEYA karşılıklı gol' gibi birleşim piyasaları."""
    result = _result_predicate(result_code)

    def build(outcome: str, _special: str | None) -> Predicate:
        code = _norm(outcome).replace(" ", "")
        both_score = _btts_predicate("YES")
        if code in _AFFIRMATIVE:
            return lambda s: result(s[0]) or both_score(s[0])
        if code in _NEGATIVE:
            return lambda s: not (result(s[0]) or both_score(s[0]))
        raise UnknownOutcome(f"Var/Yok outcome'u geçersiz: {outcome!r}")

    return build


# --------------------------------------------------------------------------- #
# Piyasa kaydı
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class MarketDef:
    """Bir bahis piyasasının anlamı."""

    id: str
    label: str
    periods: tuple[str, ...]  # yüklemin beklediği periyot sırası
    build: Builder
    bound: Bounder
    example_outcomes: tuple[str, ...]
    needs_special: bool = False
    stray_outcomes: frozenset[str] = frozenset()
    """Sağlayıcı kataloğunda bu piyasa altında görünen ama anlamı olmayan kodlar.

    Alt/Üst piyasalarının bir kısmında ``o``/``u`` yanında ``1``/``2`` de
    listeleniyor; bunlar hatalı kayıtlar. Doğrulama bunları yok sayar, çalışma
    anında böyle bir seçim gelirse yalıtılır ve uyarı verilir.
    """

    @property
    def period(self) -> str:
        return self.periods[0]


def _market(
    market_id: str,
    label: str,
    period: str,
    builder: Builder | tuple[Builder, Bounder],
    examples: tuple[str, ...],
    *,
    bounder: Bounder | None = None,
    needs_special: bool = False,
    periods: tuple[str, ...] | None = None,
) -> MarketDef:
    if isinstance(builder, tuple):
        builder, bounder = builder
    return MarketDef(
        id=market_id,
        label=label,
        periods=periods or (period,),
        build=builder,
        bound=bounder or (lambda _outcome, _special: MIN_SCORE_BOUND),
        example_outcomes=examples,
        needs_special=needs_special,
    )


# Periyot etiketi -> insan okunur ek. Çeyrek ve periyotlar yalnızca tek
# başlarına (MATCH yerleşiminde) çözülür.
_PERIOD_LABELS = {
    "FT": "",
    "HT": "İlk Yarı ",
    "2H": "İkinci Yarı ",
    **{f"Q{i}": f"{i}. Çeyrek " for i in range(1, 5)},
    **{f"P{i}": f"{i}. Periyot " for i in range(1, 6)},
}

# (son ek, etiket, yapıcı, örnekler, sbv gerekli mi, bounder)
_PERIOD_MARKETS: tuple[tuple[str, str, object, tuple[str, ...], bool], ...] = (
    ("1X2", "Maç Sonucu", _match_result, ("1", "X", "2"), False),
    ("CIFT_SANS", "Çift Şans", _double_chance, ("1X", "12", "X2"), False),
    ("DNB", "Draw No Bet", _draw_no_bet, ("1", "2"), False),
    ("ALT_UST", "Alt / Üst", _total(), ("Over", "Under"), True),
    ("ALT_UST_3WAY", "Alt / Tam / Üst", _total_3way(), ("Over", "X", "Under"), True),
    ("ALT_UST_EV", "Alt / Üst (Ev)", _total("home"), ("Over",), True),
    ("ALT_UST_DEP", "Alt / Üst (Deplasman)", _total("away"), ("Under",), True),
    ("HANDIKAP", "Handikap", _handicap(), ("1", "X", "2"), True),
    ("TEK_CIFT", "Tek / Çift", _odd_even, ("Tek", "Çift"), False),
    ("GOL_SAYISI", "Gol Sayısı", _count(), ("0", "2-3", "6+"), False),
    ("GOL_SAYISI_EV", "Ev Gol Sayısı", _count("home"), ("0", "3+"), False),
    ("GOL_SAYISI_DEP", "Deplasman Gol Sayısı", _count("away"), ("0", "3+"), False),
    ("KARSILIKLI_GOL", "Karşılıklı Gol", _both_teams_to_score, ("Var", "Yok"), False),
    ("KOMBINE", "Kombine", _combo(), ("Over and home", "Home / Yes"), True),
)


def _build_registry() -> dict[str, MarketDef]:
    markets: dict[str, MarketDef] = {}

    for period, prefix in _PERIOD_LABELS.items():
        for suffix, label, builder, examples, needs_special in _PERIOD_MARKETS:
            # Maç sonu piyasaları önek almaz; sonucunki tek istisna.
            if period == "FT":
                market_id = "MS_1X2" if suffix == "1X2" else suffix
            else:
                market_id = f"{_period_key(period)}_{suffix}"
            markets[market_id] = _market(
                market_id,
                f"{prefix}{label}",
                period,
                builder,
                examples,
                needs_special=needs_special,
            )

    # Yalnızca maç sonu için anlamlı olanlar
    markets["DOGRU_SKOR"] = _market(
        "DOGRU_SKOR",
        "Doğru Skor",
        "FT",
        _correct_score,
        ("1-0", "2-1"),
        bounder=_correct_score_bound,
    )
    markets["IY_DOGRU_SKOR"] = _market(
        "IY_DOGRU_SKOR",
        "İlk Yarı Doğru Skor",
        "HT",
        _correct_score,
        ("1-0",),
        bounder=_correct_score_bound,
    )
    markets["SONRAKI_GOL"] = _market(
        "SONRAKI_GOL",
        "Sıradaki Gol",
        "FT",
        _next_goal(),
        ("1", "X", "2"),
        needs_special=True,
    )
    markets["REST_1X2"] = _market(
        "REST_1X2",
        "Maçın Kalanı",
        "FT",
        _rest_result(),
        ("1", "X", "2"),
        needs_special=True,
    )
    markets["IY_MS"] = _market(
        "IY_MS",
        "İlk Yarı / Maç Sonucu",
        "HT",
        _half_time_full_time,
        ("1/1", "X/2"),
        periods=("HT", "FT"),
    )
    for code, name, label in (
        ("1", "EV", "Ev Kazanır"),
        ("X", "BERABERE", "Berabere"),
        ("2", "DEP", "Deplasman"),
    ):
        markets[f"{name}_VEYA_KG"] = _market(
            f"{name}_VEYA_KG",
            f"{label} veya Karşılıklı Gol",
            "FT",
            _result_or_btts(code),
            ("Yes", "No"),
        )
    return markets


def _period_key(period: str) -> str:
    return {"HT": "IY", "2H": "IY2"}.get(period, period)


MARKETS: dict[str, MarketDef] = _build_registry()

# Sağlayıcı kataloğunda bazı alt/üst piyasaları "o"/"u" yanında "1" ve "2"
# outcome'larını da listeliyor (ör. live 19). Bunlar hatalı kayıtlardır: gerçek
# seçimler yalnızca alt ve üsttür.
_STRAY_TOTAL_OUTCOMES = frozenset({"1", "2"})

MARKETS = {
    market_id: (
        replace(market, stray_outcomes=_STRAY_TOTAL_OUTCOMES) if "ALT_UST" in market_id else market
    )
    for market_id, market in MARKETS.items()
}


# --------------------------------------------------------------------------- #
# Maske üretimi
# --------------------------------------------------------------------------- #


def required_bound(
    market_id: str, outcome: str, special: str | None, siblings: tuple[str, ...] = ()
) -> int:
    """Seçimin doğru değerlendirilebilmesi için gereken skor tavanı."""
    market = MARKETS[market_id]
    if is_aggregate(outcome):
        bounds = [
            market.bound(sibling, special) for sibling in siblings if not is_aggregate(sibling)
        ]
        needed = max(bounds) if bounds else MIN_SCORE_BOUND
    else:
        needed = market.bound(outcome, special)
    return max(MIN_SCORE_BOUND, min(needed, MAX_SCORE_BOUND))


@lru_cache(maxsize=256)
def feasible_mask(space_key: tuple[str, int], home: int, away: int) -> int:
    """Maç sonu skorunun anlık skordan küçük olamayacağını ifade eden maske.

    Canlı bir maç 2-0 ise "1.5 Alt" artık kazanamaz; bu maske o seçimin hiçbir
    atomla eşleşmemesini sağlar. Yalnızca maç sonu periyodu için anlamlıdır --
    çeyrek ya da devre grupları anlık skorla doğrudan kısıtlanamaz.
    """
    space = get_space(*space_key)
    if not home and not away:
        return space.full_mask

    accessor = _accessor(space.layout, "FT")
    buffer = bytearray((len(space.atoms) + 7) // 8)
    for index, atom in enumerate(space.atoms):
        final_home, final_away = accessor(atom)
        if final_home >= home and final_away >= away:
            buffer[index >> 3] |= 1 << (index & 7)
    return int.from_bytes(buffer, "little")


@lru_cache(maxsize=8192)
def mask_for(
    market_id: str,
    outcome: str,
    special: str | None,
    space_key: tuple[str, int],
    siblings: tuple[str, ...] = (),
) -> int:
    """Seçimin kazandığı atomların bit maskesi.

    Maske bytearray üzerinden kurulur: her atom için tek bir bit yazılır ve
    sonuç tek seferde tamsayıya çevrilir. Doğrudan ``mask |= 1 << i`` yaklaşımı
    her adımda maskenin tamamını kopyaladığı için atom sayısıyla karesel
    yavaşlar.
    """
    space = get_space(*space_key)

    if is_aggregate(outcome):
        # "Others" = listelenen diğer sonuçların hiçbiri.
        covered = 0
        for sibling in siblings:
            if not is_aggregate(sibling):
                covered |= mask_for(market_id, sibling, special, space_key)
        return space.full_mask & ~covered

    market = MARKETS[market_id]
    predicate = market.build(outcome, special)
    accessors = tuple(_accessor(space.layout, period) for period in market.periods)

    buffer = bytearray((len(space.atoms) + 7) // 8)
    for index, atom in enumerate(space.atoms):
        if predicate(tuple(accessor(atom) for accessor in accessors)):
            buffer[index >> 3] |= 1 << (index & 7)
    return int.from_bytes(buffer, "little")
