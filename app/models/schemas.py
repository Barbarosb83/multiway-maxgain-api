"""API istek/yanıt şemaları (Pydantic v2).

JSON tarafında alan adları camelCase'tir (``matchId``, ``oddTypeId``,
``couponAmount``); snake_case gövdeler de kabul edilir.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

from app.services.max_gain import MAX_MATCHES, MAX_SELECTIONS_PER_MATCH

Odds = Annotated[Decimal, Field(gt=1, le=Decimal("100000"), decimal_places=4)]
Money = Annotated[Decimal, Field(gt=0, le=Decimal("100000000"), decimal_places=2)]
MatchId = Annotated[str | int, Field(union_mode="left_to_right")]


class Base(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class StrictBase(Base):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


# --------------------------------------------------------------------------- #
# İstek
# --------------------------------------------------------------------------- #


class SelectionIn(StrictBase):
    match_id: MatchId = Field(description="Maç kimliği; aynı maça birden fazla seçim gelebilir")
    odd_id: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Outcome katalogundaki tekil seçim kimliği. Verildiğinde oddTypeId ve "
            "outcome katalogdan doldurulur; outcome adının dili önemsizleşir."
        ),
    )
    odd_type_id: int | None = Field(
        default=None, ge=0, description="Piyasa kimliği; oddId verildiyse gerekmez"
    )
    outcome: str | None = Field(
        default=None, max_length=64, description="Sonuç kodu; oddId verildiyse gerekmez"
    )
    odds: Odds = Field(description="Ondalık oran; 1.00'den büyük olmalı")

    @field_validator("odds")
    @classmethod
    def _normalize_odds(cls, value: Decimal) -> Decimal:
        """Oranı en az iki ondalığa tamamlar.

        Sağlayıcı tam sayı oran gönderebiliyor (``6``); bu, oran toplamlarının
        ``"6"`` gibi tutarsız biçimde dönmesine yol açar. Daha hassas oranlar
        (``1.025``) olduğu gibi korunur.
        """
        return value if -value.as_tuple().exponent >= 2 else value.quantize(Decimal("0.01"))

    is_live: int = Field(
        default=0,
        ge=0,
        le=1,
        description="Event pre-match ise 0, live ise 1; oddTypeId o katalogda aranır.",
    )
    special_bet_value: str | None = Field(
        default=None,
        max_length=32,
        description=(
            "Piyasanın eşik değeri: Alt/Üst için '2.5', handikap için '0:1' ya da "
            "'-1.5'. Gerektiren piyasalarda zorunludur."
        ),
    )


class SystemIn(StrictBase):
    sizes: list[int] = Field(
        min_length=1,
        max_length=MAX_MATCHES,
        description="Sistem boyutları, ör. [3] => 3'lü sistem, [2,3] => 2/N + 3/N",
    )


class CouponIn(StrictBase):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "couponAmount": "100.00",
                    "stakeMode": "total",
                    "currency": "TRY",
                    "system": {"sizes": [2]},
                    "bankerMatchIds": [903],
                    "selections": [
                        {
                            "matchId": 901,
                            "oddTypeId": 1565,
                            "isLive": 0,
                            "outcome": "1",
                            "odds": "2.10",
                        },
                        {
                            "matchId": 901,
                            "oddTypeId": 1481,
                            "isLive": 0,
                            "outcome": "1X",
                            "odds": "1.30",
                        },
                        {
                            "matchId": 902,
                            "oddTypeId": 1500,
                            "isLive": 0,
                            "outcome": "Üst",
                            "specialBetValue": "2.5",
                            "odds": "2.75",
                        },
                        {
                            "matchId": 903,
                            "oddTypeId": 1565,
                            "isLive": 0,
                            "outcome": "1",
                            "odds": "1.90",
                        },
                    ],
                }
            ]
        },
    )

    selections: list[SelectionIn] = Field(
        min_length=1, max_length=MAX_MATCHES * MAX_SELECTIONS_PER_MATCH
    )
    coupon_amount: Money = Field(
        description="Kupon tutarı; stakeMode alanı bunun nasıl yorumlanacağını belirler"
    )
    stake_mode: Literal["total", "per_line"] = Field(
        default="total",
        description=(
            "'total': tutar tüm satırlara bölünür. "
            "'per_line': tutar her satır için ayrı ayrı yatırılır."
        ),
    )
    system: SystemIn | None = Field(
        default=None, description="Verilmezse tam kombine (tüm banko olmayan maçlar) varsayılır"
    )
    banker_match_ids: list[MatchId] = Field(
        default_factory=list, description="Her satırda zorunlu yer alacak maçların kimlikleri"
    )
    bonus_multiplier: Decimal | None = Field(
        default=None, gt=0, le=Decimal("100"), description="Kupon bonusu çarpanı, ör. 1.10"
    )
    max_payout_cap: Money | None = Field(
        default=None, description="Bahis şirketinin maksimum ödeme tavanı"
    )
    currency: str = Field(default="TRY", min_length=3, max_length=3, pattern=r"^[A-Za-z]{3}$")

    @model_validator(mode="after")
    def _check_selection_identity(self) -> CouponIn:
        """Her seçim ya oddId ile ya da oddTypeId + outcome ile tanımlanmalı."""
        for index, selection in enumerate(self.selections):
            if selection.odd_id is None and (
                selection.odd_type_id is None or not (selection.outcome or "").strip()
            ):
                raise ValueError(
                    f"selections[{index}]: oddId verilmediğinde oddTypeId ve outcome zorunludur."
                )
        return self

    @model_validator(mode="after")
    def _check_selections_unique(self) -> CouponIn:
        seen: set[tuple[str, int, int | None, int | None, str, str]] = set()
        for selection in self.selections:
            # specialBetValue anahtarın parçasıdır: "Üst 0.5" ile "Üst 2.5" aynı
            # piyasanın farklı çizgileridir ve birlikte oynanabilir.
            key = (
                str(selection.match_id),
                selection.is_live,
                selection.odd_id,
                selection.odd_type_id,
                (selection.outcome or "").strip().upper(),
                (selection.special_bet_value or "").strip().upper(),
            )
            if key in seen:
                raise ValueError(
                    f"Aynı seçim iki kez gönderildi: maç {selection.match_id}, "
                    f"oddId {selection.odd_id}, oddType {selection.odd_type_id}, "
                    f"outcome {selection.outcome!r}, "
                    f"specialBetValue {selection.special_bet_value!r}."
                )
            seen.add(key)

        match_ids = {str(s.match_id) for s in self.selections}
        missing = {str(m) for m in self.banker_match_ids} - match_ids
        if missing:
            raise ValueError(f"Banko olarak işaretlenen maçlar kuponda yok: {sorted(missing)}")
        return self


# --------------------------------------------------------------------------- #
# Yanıt
# --------------------------------------------------------------------------- #


class WinningSelectionOut(Base):
    odd_id: int | None = None
    odd_type_id: int
    odd_type_name: str
    is_live: int
    outcome: str
    odds: Decimal
    special_bet_value: str | None = None


class GroupResolutionOut(Base):
    group: str = Field(description="Kısıt grubu; aynı gruptaki seçimler birbirini kısıtlar")
    odds_sum: Decimal = Field(description="Bu grubun maç ağırlığına katkısı")
    combined: bool = Field(description="True ise bu grupta birden fazla seçim aynı anda kazanıyor")
    winning_selections: list[WinningSelectionOut]
    scoreline: dict[str, str] | None = Field(
        default=None,
        description=(
            "Grubu gerçekleyen örnek skor. Anahtarlar modellenen periyotlardır: "
            "`fullTime`, `halfTime`, `quarter1`, `period2` gibi. Yalıtılmış "
            "gruplarda boştur."
        ),
    )


class MatchResolutionOut(Base):
    match_id: str | int
    banker: bool
    selection_count: int = Field(description="Bu maç için gelen seçim sayısı (satır çarpanı)")
    weight: Decimal = Field(
        description="Maçın en iyi senaryodaki ağırlığı: uyumlu seçimlerin oran toplamı"
    )
    groups: list[GroupResolutionOut]


class SizeBreakdownOut(Base):
    system_size: int
    line_count: int
    gross_gain: Decimal


class StakeOut(Base):
    total: Decimal = Field(description="Kuponun toplam maliyeti")
    per_line: Decimal = Field(
        description=(
            "Kupon tutarının satır sayısına bölünmüş hâli. Ödenen bir tutar değil, "
            "türetilmiş bir bölüşüm oranıdır; büyük sistemlerde kuruşun altına "
            "inebildiği için 6 ondalık hassasiyetle döner."
        )
    )
    line_count: int = Field(description="Kuponun açıldığı toplam satır (way) sayısı")


class MaxGainOut(Base):
    currency: str
    stake: StakeOut
    max_gain: Decimal = Field(description="En iyi senaryoda kazanan tüm satırların toplam ödemesi")
    net_profit: Decimal = Field(description="maxGain - toplam stake")
    max_single_line_gain: Decimal = Field(
        description="Tek bir satırdan gelebilecek en yüksek ödeme"
    )
    effective_multiplier: Decimal = Field(description="maxGain / toplam stake")
    capped: bool = Field(description="Ödeme tavanı uygulandıysa true")
    matches: list[MatchResolutionOut]
    breakdown: list[SizeBreakdownOut]
    warnings: list[str] = Field(default_factory=list)


class OddTypeOut(Base):
    odd_type_id: int
    is_live: int
    name: str
    mapped: bool = Field(description="False ise anlamı eşlenmemiş; hesapta geri düşüş uygulanır")
    market_id: str | None = None
    market_label: str | None = None
    example_outcomes: list[str] = Field(default_factory=list)
    needs_special_bet_value: bool = False


class OddTypePage(Base):
    total: int
    limit: int
    offset: int
    items: list[OddTypeOut]


class ErrorOut(Base):
    detail: str
