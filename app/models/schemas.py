"""API istek/yanıt şemaları (Pydantic v2)."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_EVENTS = 50
MAX_SELECTIONS_PER_EVENT = 20

Odds = Annotated[Decimal, Field(gt=1, le=Decimal("100000"), decimal_places=4)]
Money = Annotated[Decimal, Field(gt=0, le=Decimal("100000000"), decimal_places=2)]


class SelectionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(
        min_length=1, max_length=64, description="Seçim (outcome) kimliği, ör. '1', 'X', 'over_2_5'"
    )
    odds: Odds = Field(description="Ondalık oran; 1.00'den büyük olmalı")
    name: str | None = Field(default=None, max_length=200, description="İnsan okunur seçim adı")


class EventIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64, description="Event (maç) kimliği")
    selections: list[SelectionIn] = Field(
        min_length=1,
        max_length=MAX_SELECTIONS_PER_EVENT,
        description="Bu event için işaretlenen, birbirini dışlayan seçimler",
    )
    name: str | None = Field(default=None, max_length=200)
    banker: bool = Field(
        default=False, description="True ise bu event her satırda zorunlu yer alır"
    )


class SystemIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sizes: list[int] = Field(
        min_length=1,
        max_length=MAX_EVENTS,
        description="Sistem boyutları, ör. [3] => 3'lü sistem, [2,3] => 2/N + 3/N",
    )


class CouponIn(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "stake": "100.00",
                    "stake_mode": "total",
                    "currency": "TRY",
                    "system": {"sizes": [2]},
                    "events": [
                        {
                            "id": "m1",
                            "name": "Galatasaray - Fenerbahçe",
                            "selections": [
                                {"id": "1", "name": "MS 1", "odds": "2.10"},
                                {"id": "X", "name": "MS X", "odds": "3.40"},
                            ],
                        },
                        {
                            "id": "m2",
                            "name": "Beşiktaş - Trabzonspor",
                            "selections": [{"id": "2", "name": "MS 2", "odds": "2.75"}],
                        },
                        {
                            "id": "m3",
                            "name": "Real Madrid - Barcelona",
                            "banker": True,
                            "selections": [{"id": "1", "name": "MS 1", "odds": "1.90"}],
                        },
                    ],
                }
            ]
        },
    )

    events: list[EventIn] = Field(min_length=1, max_length=MAX_EVENTS)
    stake: Money = Field(
        description="Yatırılan tutar; stake_mode alanı nasıl yorumlanacağını belirler"
    )
    stake_mode: Literal["total", "per_line"] = Field(
        default="total",
        description=(
            "'total': tutar tüm satırlara bölünür. "
            "'per_line': tutar her satır için ayrı ayrı yatırılır."
        ),
    )
    system: SystemIn | None = Field(
        default=None,
        description="Verilmezse tam kombine (tüm banko olmayan event'ler tek satırda) varsayılır",
    )
    bonus_multiplier: Decimal | None = Field(
        default=None,
        gt=0,
        le=Decimal("100"),
        description="Kupon bonusu çarpanı, ör. 1.10 => %10 bonus",
    )
    max_payout_cap: Money | None = Field(
        default=None, description="Bahis şirketinin maksimum ödeme tavanı"
    )
    currency: str = Field(default="TRY", min_length=3, max_length=3, pattern=r"^[A-Za-z]{3}$")

    @model_validator(mode="after")
    def _check_ids_unique(self) -> CouponIn:
        event_ids = [e.id for e in self.events]
        duplicates = {i for i in event_ids if event_ids.count(i) > 1}
        if duplicates:
            raise ValueError(f"Tekrar eden event id: {sorted(duplicates)}")
        for event in self.events:
            selection_ids = [s.id for s in event.selections]
            dupes = {i for i in selection_ids if selection_ids.count(i) > 1}
            if dupes:
                raise ValueError(f"{event.id!r} event'inde tekrar eden seçim id: {sorted(dupes)}")
        return self


class BestPickOut(BaseModel):
    event_id: str
    event_name: str | None = None
    selection_id: str
    selection_name: str | None = None
    odds: Decimal
    banker: bool


class SizeBreakdownOut(BaseModel):
    system_size: int
    line_count: int
    gross_gain: Decimal


class StakeOut(BaseModel):
    total: Decimal = Field(description="Kuponun toplam maliyeti")
    per_line: Decimal = Field(
        description=(
            "Toplam stake'in satır sayısına bölünmüş hâli. Ödenen bir tutar değil, "
            "türetilmiş bir bölüşüm oranıdır; büyük sistemlerde kuruşun altına "
            "inebildiği için 6 ondalık hassasiyetle döner."
        )
    )
    line_count: int = Field(description="Kuponun açıldığı toplam satır (way) sayısı")


class MaxGainOut(BaseModel):
    currency: str
    stake: StakeOut
    max_gain: Decimal = Field(description="En iyi senaryoda kazanan tüm satırların toplam ödemesi")
    net_profit: Decimal = Field(description="max_gain - toplam stake")
    max_single_line_gain: Decimal = Field(
        description="Tek bir satırdan gelebilecek en yüksek ödeme"
    )
    effective_multiplier: Decimal = Field(description="max_gain / toplam stake")
    capped: bool = Field(description="Ödeme tavanı uygulandıysa true")
    best_scenario: list[BestPickOut]
    breakdown: list[SizeBreakdownOut]
    warnings: list[str] = Field(default_factory=list)


class ErrorOut(BaseModel):
    detail: str
    code: str = "coupon_invalid"
