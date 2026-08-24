"""oddTypeId -> piyasa eşlemesi.

Gelen kuponda her seçim bir ``oddTypeId`` taşır. Bu modül o id'nin hangi
piyasaya karşılık geldiğini tutar; piyasanın *anlamı* ``app.services.markets``
içindedir.

Katalogda olmayan bir id gelirse istek reddedilmez. Bunun yerine güvenli bir
geri düşüş uygulanır:

* aynı ``oddTypeId``'nin farklı outcome'ları birbirini dışlar  -> en yüksek oran
* farklı ``oddTypeId``'ler bağımsız kabul edilir               -> oranlar toplanır

Bu, katalog eksikken bile makul sonuç verir; yalnızca *farklı* oddType'ların
birbiriyle çeliştiği durumları (ör. 1X2 "1" ile Çift Şans "X2") kaçırır.
O yüzden yanıt ``warnings`` alanında bilinmeyen id'ler açıkça bildirilir.

Yeni bir id eklemek tek satırdır::

    3: "ALT_UST",
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.markets import MARKETS, MarketDef

__all__ = ["ODD_TYPE_MARKET", "OddTypeInfo", "resolve_odd_type", "catalog"]


# --------------------------------------------------------------------------- #
# oddTypeId -> markets.MARKETS anahtarı
#
# Buradaki id'ler sağlayıcının kataloğundan gelir. Doğrulanmamış bir id eklemek,
# çelişen seçimlerin yanlışlıkla uyumlu sayılmasına yol açabileceği için yalnızca
# teyit edilenler listelenir.
# --------------------------------------------------------------------------- #
ODD_TYPE_MARKET: dict[int, str] = {
    1: "MS_1X2",  # Maç Sonucu (1X2)      -- teyitli
    2: "CIFT_SANS",  # Çift Şans          -- teyitli
}


@dataclass(frozen=True)
class OddTypeInfo:
    """Bir ``oddTypeId``'nin çözümlenmiş hâli."""

    odd_type_id: int
    name: str
    market: MarketDef | None  # None => katalogda yok, geri düşüş uygulanacak

    @property
    def known(self) -> bool:
        return self.market is not None

    @property
    def group(self) -> str:
        """Seçimin ait olduğu kısıt grubu.

        Bilinen piyasalar ortak sonuç uzayını paylaşır. Bilinmeyen her id kendi
        yalıtılmış grubunda yaşar; böylece aynı id'nin outcome'ları dışlayıcı,
        farklı id'ler bağımsız olur.
        """
        return self.market.group if self.market else f"UNKNOWN:{self.odd_type_id}"


def resolve_odd_type(odd_type_id: int) -> OddTypeInfo:
    market_id = ODD_TYPE_MARKET.get(odd_type_id)
    if market_id is None:
        return OddTypeInfo(odd_type_id, f"Bilinmeyen oddType ({odd_type_id})", None)
    market = MARKETS[market_id]
    return OddTypeInfo(odd_type_id, market.label, market)


def catalog() -> list[dict[str, object]]:
    """Katalogdaki tüm oddType'ları, örnek outcome'larıyla birlikte döner."""
    rows = []
    for odd_type_id, market_id in sorted(ODD_TYPE_MARKET.items()):
        market = MARKETS[market_id]
        rows.append(
            {
                "odd_type_id": odd_type_id,
                "name": market.label,
                "market_id": market.id,
                "group": market.group,
                "example_outcomes": list(market.example_outcomes),
            }
        )
    return rows
