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
    "OddTypeInfo",
    "resolve_odd_type",
    "resolve_outcome",
    "catalog",
    "catalog_size",
    "ODD_TYPE_MARKET",
    "REJECTED_MAPPINGS",
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


ODD_TYPE_NAME = _load_names()
OUTCOME_BY_ODD_ID, OUTCOMES_BY_ODD_TYPE = _load_outcomes()


def _sample_special(market: MarketDef) -> str | None:
    """Doğrulama için temsilî specialBetValue."""
    if not market.needs_special:
        return None
    return "0:1" if "HANDIKAP" in market.id else "2.5"


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
        market_id = _NAME_TO_MARKET.get(_normalize(name))
        if market_id is None:
            continue

        market = MARKETS[market_id]
        special = _sample_special(market)
        problem = ""
        for outcome in OUTCOMES_BY_ODD_TYPE.get(key, []):
            if is_aggregate(outcome):
                continue  # tümleyen olarak çözülür, piyasa yüklemi gerekmez
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
    (1, 19): (
        "Outcome kümesi '1, 2, o, u' -- '1' ve '2'nin Üst/Alt karşılığı "
        "belgelenmemiş, tahmin edilirse yön ters çevrilebilir."
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
