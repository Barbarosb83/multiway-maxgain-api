"""oddTypeId -> piyasa eşlemesi.

Sağlayıcı iki ayrı katalog kullanır ve **id uzayları örtüşmez değil, ayrıdır**:
pre-match id'leri ve live id'leri bağımsız numaralandırılmıştır. Bu yüzden bir
oddType daima ``(isLive, oddTypeId)`` çiftiyle çözümlenir; kuponda ``isLive``
alanı pre için ``0``, live için ``1`` gelir.

Adlar ``data/odd_types_pre.csv`` ve ``data/odd_types_live.csv`` dosyalarından
okunur; böylece 1400'den fazla id'nin tamamı isimleriyle görünür
(``GET /api/v1/odd-types``).

Anlamsal eşleme
---------------
Katalogda aynı ad birden çok id'de tekrar eder (farklı sporlar için ayrı
id'ler). Bu yüzden eşleme id yerine **ad** üzerinden kurulur: bir id'nin adı
``_NAME_TO_MARKET`` tablosundaki bir girdiyle birebir eşleşiyorsa o piyasanın
anlamı kullanılır.

Sporlar arası karışma ağırlığı bozmaz: aynı ``matchId`` altındaki seçimler
daima aynı spora aittir, dolayısıyla farklı sporların id'leri asla aynı maçta
karşılaşmaz. Buna karşılık **birimi belirsiz** piyasalar (``Handicap``,
``Over/Under``, ``Total``, ``Asian Total`` ...) bilerek eşlenmez: bunların
eşiği futbolda gol, basketbolda sayı demektir ve yanlış eşleme sessiz hataya
yol açar. Eşlenmeyen id'ler reddedilmez, geri düşüşe girer.

Geri düşüş
----------
Eşlenmemiş bir id için:

* aynı ``(isLive, oddTypeId)``'nin farklı outcome'ları dışlayıcı -> en yüksek oran
* farklı id'ler bağımsız                                          -> oranlar toplanır

Bu, kullanıcının belirttiği basit kuralın ta kendisidir; yalnızca *farklı*
id'lerin birbiriyle çeliştiği durumları kaçırır ve ``warnings`` ile bildirilir.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

from app.services.markets import MARKETS, MarketDef, UnknownOutcome, is_aggregate

__all__ = [
    "SCORE_BASED_MARKETS",
    "OddTypeInfo",
    "resolve_odd_type",
    "resolve_outcome",
    "catalog",
    "catalog_size",
    "ODD_TYPE_MARKET",
    "REJECTED_MAPPINGS",
    "sample_special_bet_value",
]

_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
_SOURCES = {0: _DATA_DIR / "odd_types_pre.csv", 1: _DATA_DIR / "odd_types_live.csv"}

_WS = re.compile(r"\s+")


def _normalize(name: str) -> str:
    """Ad eşlemesi için kanonik biçim: küçük harf, tire ve boşluklar sadeleşmiş."""
    text = name.strip().lower()
    for dash in ("–", "—", "−"):  # en/em dash, eksi işareti
        text = text.replace(dash, "-")
    return _WS.sub(" ", text)


# --------------------------------------------------------------------------- #
# Ad -> piyasa tablosu
#
# Yalnızca anlamı tereddütsüz olan piyasalar yer alır. Sonuç ailesi (1X2, çift
# şans, draw no bet) sporlar arasında yapı olarak aynıdır ve güvenlidir; gol
# bazlı olanlar ise adında "goal" geçtiği için futbola özgüdür.
# --------------------------------------------------------------------------- #
_NAME_TO_MARKET: dict[str, str] = {
    # --- Maç sonucu ailesi (tam maç) ---
    "3way": "MS_1X2",
    "3 way": "MS_1X2",
    "1x2": "MS_1X2",
    "winner": "MS_1X2",
    "rest of match": "REST_1X2",
    "next goal": "SONRAKI_GOL",
    "3way aams": "MS_1X2",
    "total 3way": "ALT_UST_3WAY",
    "double chance": "CIFT_SANS",
    "double chance (all)": "CIFT_SANS",
    "double chance (1x - 12 - x2)": "CIFT_SANS",
    "draw no bet": "DNB",
    # --- İlk yarı ---
    "1st half -1x2": "IY_1X2",
    "1st half - 1x2": "IY_1X2",
    "1st half - 3way": "IY_1X2",
    "halftime - 3way": "IY_1X2",
    "1st half - double chance": "IY_CIFT_SANS",
    "1st half double chance": "IY_CIFT_SANS",
    "halftime - double chance (1x - 12 - x2)": "IY_CIFT_SANS",
    "1st half - draw no bet": "IY_DNB",
    "1st half draw no bet": "IY_DNB",
    "draw no bet first half": "IY_DNB",
    "draw no bet for first half": "IY_DNB",
    # --- İkinci yarı ---
    "2nd half - 1x2": "IY2_1X2",
    "2nd half - 3way": "IY2_1X2",
    "2nd half - double chance": "IY2_CIFT_SANS",
    "2nd half - double chance (1x - 12 - x2)": "IY2_CIFT_SANS",
    "2nd half - draw no bet": "IY2_DNB",
    # --- Alt / Üst (eşik specialBetValue'dan gelir) ---
    "over/under": "ALT_UST",
    "total": "ALT_UST",
    "totals": "ALT_UST",
    "asian total": "ALT_UST",
    "total goals": "GOL_SAYISI",
    "total aams": "ALT_UST",
    "total goals aams": "GOL_SAYISI",
    "1st half - over/under": "IY_ALT_UST",
    "1st half - total": "IY_ALT_UST",
    "1st half - totals": "IY_ALT_UST",
    "1st half - asian total": "IY_ALT_UST",
    "1st half - total goals": "IY_GOL_SAYISI",
    "2nd half - total": "IY2_ALT_UST",
    "2nd half - totals": "IY2_ALT_UST",
    "2nd half - asian total": "IY2_ALT_UST",
    "total spreads": "ALT_UST",
    "total spreads (excl. superovers)": "ALT_UST",
    "us total": "ALT_UST",
    "2nd half - total spread": "IY2_ALT_UST",
    # AAMS varyantları düzenli oyun süresini kapsar -- tam maç piyasalarıdır
    "total aams regular time": "ALT_UST",
    "matchbet aams regular time": "MS_1X2",
    "2nd half - total goals": "IY2_GOL_SAYISI",
    # --- Takım bazlı alt / üst ---
    "totals home": "ALT_UST_EV",
    "totals home team": "ALT_UST_EV",
    "total hometeam": "ALT_UST_EV",
    "goals home": "GOL_SAYISI_EV",
    "goals home team": "GOL_SAYISI_EV",
    "totals away": "ALT_UST_DEP",
    "totals away team": "ALT_UST_DEP",
    "total awayteam": "ALT_UST_DEP",
    "goals away": "GOL_SAYISI_DEP",
    "goals away team": "GOL_SAYISI_DEP",
    # --- Handikap ---
    # Asya handikabında 0 ve çeyrek çizgilerde iade/yarım kazanç vardır; burada
    # kazanır/kazanmaz olarak ele alınır. Bu, max gain'i düşürebilir ama asla
    # şişirmez.
    "handicap": "HANDIKAP",
    "asian handicap": "HANDIKAP",
    "european handicap": "HANDIKAP",
    "asian handicap main line": "HANDIKAP",
    "1st half - handicap": "IY_HANDIKAP",
    "1st half - asian handicap": "IY_HANDIKAP",
    "1st half - european handicap": "IY_HANDIKAP",
    # Bu sağlayıcıda "Spread" handikap/alt-üst anlamına gelir
    "goal spreads": "HANDIKAP",
    "goal spread main line": "HANDIKAP",
    "points spread": "HANDIKAP",
    "points spreads": "HANDIKAP",
    "points spread main line": "HANDIKAP",
    "us spread": "HANDIKAP",
    "1st half - goal spread": "IY_HANDIKAP",
    "1st half - points spread": "IY_HANDIKAP",
    "1st half - points spreads": "IY_HANDIKAP",
    "2nd half - points spread": "IY2_HANDIKAP",
    "2nd half - handicap": "IY2_HANDIKAP",
    "asian handicap first half": "IY_HANDIKAP",
    "asian handicap 1st half": "IY_HANDIKAP",
    # --- Gol bazlı ---
    "both teams to score": "KARSILIKLI_GOL",
    "both score": "KARSILIKLI_GOL",
    "goal/no goal": "KARSILIKLI_GOL",
    "odd / even goals": "TEK_CIFT",
    "odd/even goals": "TEK_CIFT",
    "odd/even": "TEK_CIFT",
    "1st half - odd/even goals": "IY_TEK_CIFT",
    "1st half - odd / even": "IY_TEK_CIFT",
    "odd/even for first half": "IY_TEK_CIFT",
    "multigoals": "GOL_SAYISI",
    "1st half - multigoals": "IY_GOL_SAYISI",
    "2nd half - multigoals": "IY2_GOL_SAYISI",
    "multigoals hometeam": "GOL_SAYISI_EV",
    "multigoals awayteam": "GOL_SAYISI_DEP",
    "total goals (exact)": "GOL_SAYISI",
    "exact number of goals": "GOL_SAYISI",
    "1st half - exact number of goals": "IY_GOL_SAYISI",
    "goals home team for [periodnr!] period": "GOL_SAYISI_EV",
    "1st half - goals hometeam": "IY_GOL_SAYISI_EV",
    "1st half - goals awayteam": "IY_GOL_SAYISI_DEP",
    # --- Doğru skor ve İY/MS ---
    "correct score": "DOGRU_SKOR",
    "correctscore": "DOGRU_SKOR",
    "correct score aams": "DOGRU_SKOR",
    "halftime / fulltime": "IY_MS",
    "halftime/fulltime": "IY_MS",
    "half time / full time": "IY_MS",
}


_OUTCOME_SOURCES = {0: _DATA_DIR / "outcomes_pre.csv", 1: _DATA_DIR / "outcomes_live.csv"}


def _read_rows(path: Path) -> list[list[str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [row for row in csv.reader(handle) if row and row[0].strip().isdigit()]


def _period_variants() -> dict[str, str]:
    """Çeyrek ve periyot piyasalarının ad eşlemelerini üretir.

    Katalogda bunlar hem "1st Quarter - Points Spread" hem "Total for first
    period" gibi iki ayrı kalıpta geçiyor; ikisi de üretilir.
    """
    ordinals = ["first", "second", "third", "fourth", "fifth"]
    short = ["1st", "2nd", "3rd", "4th", "5th"]
    suffixes = {
        "1x2": "1X2",
        "3way": "1X2",
        "matchbet aams": "1X2",
        "draw no bet": "DNB",
        "draw nobet": "DNB",
        "double chance": "CIFT_SANS",
        "total": "ALT_UST",
        "totals": "ALT_UST",
        "asian total": "ALT_UST",
        "total spread": "ALT_UST",
        "total aams": "ALT_UST",
        "points spread": "HANDIKAP",
        "goal spreads": "HANDIKAP",
        "handicap": "HANDIKAP",
        "asian handicap": "HANDIKAP",
        "odd/even": "TEK_CIFT",
        "odd/even points": "TEK_CIFT",
        "odd/even goals": "TEK_CIFT",
        "both teams to score": "KARSILIKLI_GOL",
    }

    table: dict[str, str] = {}
    for index in range(5):
        for kind, prefix in (("Q", "quarter"), ("P", "period")):
            if kind == "Q" and index >= 4:
                continue
            key = f"{kind}{index + 1}"
            for suffix, market_suffix in suffixes.items():
                market_id = f"{key}_{market_suffix}"
                if market_id not in MARKETS:
                    continue
                # "1st Quarter - Points Spread" ve "1st Quarter 1X2"
                table[f"{short[index]} {prefix} - {suffix}"] = market_id
                table[f"{short[index]} {prefix} {suffix}"] = market_id
                # "Total for first period"
                table[f"{suffix} for {ordinals[index]} {prefix}"] = market_id
                table[f"{suffix} {ordinals[index]} {prefix}"] = market_id
    return table


# Kombine ve birleşim piyasaları. Outcome'ları "Over and home", "Home / Yes",
# "DrawAway / Under" gibi iki bileşenlidir; ayrıştırıcı bileşenleri tanır.
_COMBO_NAMES: dict[str, str] = {
    "matchbet and totals": "KOMBINE",
    "matchbet + totals": "KOMBINE",
    "matchbet and total": "KOMBINE",
    "matchbet and both teams to score": "KOMBINE",
    "matchbet + both teams to score": "KOMBINE",
    "both score + totals": "KOMBINE",
    "double chance and total": "KOMBINE",
    "double chance and both teams score": "KOMBINE",
    "double chance (1x - 12 - x2) and totals": "KOMBINE",
    "double chance (1x - 12 - x2) and both teams to score": "KOMBINE",
    "1st half - matchbet and both teams to score": "IY_KOMBINE",
    "first half - matchbet + totals": "IY_KOMBINE",
    "first half - matchbet + both teams to score": "IY_KOMBINE",
    "first half - matchbet and totals [total]": "IY_KOMBINE",
    "1st half - double chance and both teams score": "IY_KOMBINE",
    "2nd half - matchbet and total": "IY2_KOMBINE",
    "2nd half - matchbet and totals": "IY2_KOMBINE",
    "2nd half - matchbet and both teams score": "IY2_KOMBINE",
    "2nd half - matchbet and both teams to score": "IY2_KOMBINE",
    "2nd half - double chance and both teams score": "IY2_KOMBINE",
    # Birleşim (VEYA) piyasaları
    "home or both teams to score": "EV_VEYA_KG",
    "draw or both teams to score": "BERABERE_VEYA_KG",
    "away or both teams to score": "DEP_VEYA_KG",
}


def _load_names() -> dict[tuple[int, int], str]:
    names: dict[tuple[int, int], str] = {}
    for is_live, path in _SOURCES.items():
        for row in _read_rows(path):
            if len(row) >= 2:
                names[(is_live, int(row[0]))] = ",".join(row[1:]).strip()
    return names


def _load_outcomes() -> tuple[
    dict[tuple[int, int], tuple[int, str]], dict[tuple[int, int], list[str]]
]:
    """oddId -> (oddTypeId, outcome adı) ve oddTypeId -> outcome adları."""
    by_odd_id: dict[tuple[int, int], tuple[int, str]] = {}
    by_odd_type: dict[tuple[int, int], list[str]] = {}
    for is_live, path in _OUTCOME_SOURCES.items():
        for row in _read_rows(path):
            if len(row) < 3 or not row[1].strip().isdigit():
                continue
            odd_type_id, odd_id = int(row[0]), int(row[1])
            outcome = ",".join(row[2:]).strip()
            by_odd_id[(is_live, odd_id)] = (odd_type_id, outcome)
            by_odd_type.setdefault((is_live, odd_type_id), []).append(outcome)
    return by_odd_id, by_odd_type


_EFFECTIVE_NAME_MAP: dict[str, str] = {
    **_NAME_TO_MARKET,
    **_COMBO_NAMES,
    **_period_variants(),
}

ODD_TYPE_NAME = _load_names()
OUTCOME_BY_ODD_ID, OUTCOMES_BY_ODD_TYPE = _load_outcomes()


# specialBetValue'su eşik değil, anlık skor olan piyasalar.
_SCORE_BASED_MARKETS = {"REST_1X2", "SONRAKI_GOL"}
SCORE_BASED_MARKETS = frozenset(_SCORE_BASED_MARKETS)


def sample_special_bet_value(market: MarketDef) -> str | None:
    """Doğrulama için temsilî specialBetValue.

    Piyasa ailesine göre değişir: handikap bir skor farkı, canlı "maçın kalanı"
    piyasaları anlık skor, alt/üst ise sayısal bir eşik bekler.
    """
    if not market.needs_special:
        return None
    if "HANDIKAP" in market.id:
        return "0:1"
    if market.id in _SCORE_BASED_MARKETS:
        return "0:0"
    return "2.5"


def _build_market_map() -> tuple[dict[tuple[int, int], str], list[tuple[int, int, str, str]]]:
    """Ad tablosunu uygular ve her eşlemeyi gerçek outcome kümesiyle doğrular.

    Bir oddType'ın outcome'larından herhangi biri piyasa tanımıyla
    çözümlenemiyorsa eşleme kabul edilmez: yanlış bir eşleme, çelişen
    seçimleri sessizce uyumlu gösterip max gain'i şişirebilir. Reddedilenler
    ``REJECTED_MAPPINGS`` içinde görünür ve testle raporlanır.
    """
    markets: dict[tuple[int, int], str] = {}
    rejected: list[tuple[int, int, str, str]] = []

    for key, name in ODD_TYPE_NAME.items():
        market_id = _EFFECTIVE_NAME_MAP.get(_normalize(name))
        if market_id is None:
            continue

        market = MARKETS[market_id]
        special = sample_special_bet_value(market)
        problem = ""
        for outcome in OUTCOMES_BY_ODD_TYPE.get(key, []):
            if is_aggregate(outcome) or outcome.strip() in market.stray_outcomes:
                continue  # tümleyen ya da hatalı katalog kaydı
            try:
                market.build(outcome, special)
            except UnknownOutcome as exc:
                problem = str(exc)
                break

        if problem:
            rejected.append((key[0], key[1], name, problem))
        else:
            markets[key] = market_id

    return markets, rejected


ODD_TYPE_MARKET, REJECTED_MAPPINGS = _build_market_map()

# Adı bir piyasaya işaret ettiği hâlde outcome kümesi o piyasayla bağdaşmayan,
# bilerek eşlenmemiş id'ler. Doğrulama bunları zaten reddeder; burada gerekçesi
# kayda geçer ve test, listenin bundan ibaret kaldığını doğrular.
KNOWN_UNMAPPABLE: dict[tuple[int, int], str] = {
    (0, 1875): (
        "Outcome'lar kesirli set skorları ('0.5:1.5'); gol skoru değil, set/leg handikabı."
    ),
    (1, 180): (
        "Adı 'Winner' ama outcome'lar 'competitor_1..14'; tek maç değil, "
        "çok yarışmacılı outright piyasası."
    ),
}


def resolve_outcome(odd_id: int, is_live: int = 0) -> tuple[int, str] | None:
    """oddId -> (oddTypeId, outcome adı). Bilinmiyorsa None."""
    return OUTCOME_BY_ODD_ID.get((is_live, odd_id))


@dataclass(frozen=True)
class OddTypeInfo:
    """Bir ``(isLive, oddTypeId)`` çiftinin çözümlenmiş hâli."""

    odd_type_id: int
    is_live: int
    name: str
    market: MarketDef | None  # None => anlamı eşlenmemiş, geri düşüş uygulanır

    @property
    def known(self) -> bool:
        return self.market is not None

    @property
    def in_catalog(self) -> bool:
        """Adı katalogda var mı? (Anlamı eşlenmemiş olsa bile.)"""
        return (self.is_live, self.odd_type_id) in ODD_TYPE_NAME

    @property
    def name_in_other_namespace(self) -> str | None:
        """Id kendi katalogunda yoksa diğerinde var mı?

        Varsa bu, ``isLive`` bayrağının seçime uymadığına işaret eder: pre-match
        id'si canlı olarak, ya da tersi gönderilmiştir.
        """
        if self.in_catalog:
            return None
        return ODD_TYPE_NAME.get((1 - self.is_live, self.odd_type_id))

    @property
    def group(self) -> str:
        """Seçimin ait olduğu kısıt grubu.

        Anlamı bilinen piyasalar ortak sonuç uzayını paylaşır. Eşlenmemiş her id
        kendi yalıtılmış grubunda yaşar; böylece aynı id'nin outcome'ları
        dışlayıcı, farklı id'ler bağımsız olur.
        """
        if self.market:
            return self.market.group
        return f"UNMAPPED:{self.is_live}:{self.odd_type_id}"


def resolve_odd_type(odd_type_id: int, is_live: int = 0) -> OddTypeInfo:
    key = (is_live, odd_type_id)
    name = ODD_TYPE_NAME.get(key)
    market_id = ODD_TYPE_MARKET.get(key)
    return OddTypeInfo(
        odd_type_id=odd_type_id,
        is_live=is_live,
        name=name or f"Bilinmeyen oddType ({odd_type_id})",
        market=MARKETS[market_id] if market_id else None,
    )


def catalog_size(is_live: int | None = None, query: str | None = None) -> int:
    return len(_filtered(is_live, query))


def _filtered(is_live: int | None, query: str | None) -> list[tuple[int, int]]:
    needle = _normalize(query) if query else None
    keys = [
        key
        for key in ODD_TYPE_NAME
        if (is_live is None or key[0] == is_live)
        and (needle is None or needle in _normalize(ODD_TYPE_NAME[key]))
    ]
    return sorted(keys)


def catalog(
    is_live: int | None = None,
    query: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, object]]:
    """Katalogdaki oddType'ları listeler.

    ``mapped`` alanı, o id'nin anlamsal bir piyasaya bağlanıp bağlanmadığını
    gösterir; ``false`` ise hesapta geri düşüş uygulanır.
    """
    rows = []
    for key in _filtered(is_live, query)[offset : offset + limit]:
        live_flag, odd_type_id = key
        market_id = ODD_TYPE_MARKET.get(key)
        market = MARKETS[market_id] if market_id else None
        rows.append(
            {
                "odd_type_id": odd_type_id,
                "is_live": live_flag,
                "name": ODD_TYPE_NAME[key],
                "mapped": market is not None,
                "market_id": market.id if market else None,
                "market_label": market.label if market else None,
                "example_outcomes": list(market.example_outcomes) if market else [],
                "needs_special_bet_value": market.needs_special if market else False,
            }
        )
    return rows
