"""HTTP uç noktaları."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, HTTPException, Query
from pydantic.alias_generators import to_camel

from app.core.config import settings
from app.models.schemas import CouponIn, ErrorOut, MaxGainOut, OddTypeOut, OddTypePage
from app.services.max_gain import (
    CouponError,
    CouponInput,
    MaxGainResult,
    SelectionInput,
    calculate_max_gain,
)
from app.services.odd_types import catalog, catalog_size

# Swagger'da açılır listede görünen hazır gövdeler. Üçü de gerçek kupon
# yapılarından alınmıştır.
COUPON_EXAMPLES = {
    "multiway": {
        "summary": "Multiway (pre-match)",
        "description": (
            "Üç maç, her birinde iki seçim. Konyaspor maçında maç sonucu '1' ile "
            "çift şans 'X2' çelişir, düşük oranlı olan elenir."
        ),
        "value": {
            "couponAmount": "100.00",
            "selections": [
                {
                    "matchId": 72723168,
                    "isLive": 0,
                    "oddTypeId": 1839,
                    "outcome": "1",
                    "odds": "2.15",
                    "currentScore": "0:0",
                },
                {
                    "matchId": 72723168,
                    "isLive": 0,
                    "oddTypeId": 1467,
                    "outcome": "N",
                    "odds": "2.05",
                    "currentScore": "0:0",
                },
                {
                    "matchId": 72723170,
                    "isLive": 0,
                    "oddTypeId": 1839,
                    "outcome": "1",
                    "odds": "2.00",
                    "currentScore": "0:0",
                },
                {
                    "matchId": 72723170,
                    "isLive": 0,
                    "oddTypeId": 1481,
                    "outcome": "X2",
                    "odds": "1.70",
                    "currentScore": "0:0",
                },
                {
                    "matchId": 72723172,
                    "isLive": 0,
                    "oddTypeId": 1513,
                    "outcome": "1",
                    "specialBetValue": "(0:1)",
                    "odds": "1.85",
                    "currentScore": "0:0",
                },
                {
                    "matchId": 72723172,
                    "isLive": 0,
                    "oddTypeId": 1899,
                    "outcome": "1X/Y",
                    "odds": "2.10",
                    "currentScore": "0:0",
                },
            ],
        },
    },
    "canli": {
        "summary": "Canlı kupon (anlık skorlu)",
        "description": (
            "isLive=1 ve her seçimde currentScore. Jong Utrecht 0:2 geride olduğu "
            "için ev galibiyeti 3+ gol gerektirir ve '3.5 Alt' ile çelişir. "
            "Anlık skor temelli piyasalarda (sıradaki gol, maçın kalanı) skor "
            "ayrıca specialBetValue'da da gelir."
        ),
        "value": {
            "couponAmount": "100.00",
            "selections": [
                {
                    "matchId": -13996108,
                    "isLive": 1,
                    "oddTypeId": 3,
                    "outcome": "1",
                    "specialBetValue": "0:0",
                    "odds": "2.05",
                    "currentScore": "0:0",
                },
                {
                    "matchId": -13996108,
                    "isLive": 1,
                    "oddTypeId": 11,
                    "outcome": "2",
                    "specialBetValue": "0:0",
                    "odds": "2.45",
                    "currentScore": "0:0",
                },
                {
                    "matchId": -13996109,
                    "isLive": 1,
                    "oddTypeId": 710,
                    "outcome": "Under",
                    "specialBetValue": "3.5",
                    "odds": "2.70",
                    "currentScore": "0:2",
                },
                {
                    "matchId": -13996109,
                    "isLive": 1,
                    "oddTypeId": 708,
                    "outcome": "1",
                    "odds": "30.00",
                    "currentScore": "0:2",
                },
            ],
        },
    },
    "sistem-banko": {
        "summary": "Sistem + banko",
        "description": (
            "Dört maç, biri banko. 72723172 her satırda yer alır; sistem kalan üç "
            "maça uygulanır. Seçimler yalnızca oddId ile tanımlanmış -- oddTypeId "
            "ve outcome katalogdan doldurulur."
        ),
        "value": {
            "couponAmount": "100.00",
            "system": {"sizes": [2, 3]},
            "bankerMatchIds": [72723172],
            "selections": [
                {"matchId": 72723168, "isLive": 0, "oddId": 1970, "odds": "2.15"},
                {"matchId": 72723170, "isLive": 0, "oddId": 1970, "odds": "2.00"},
                {"matchId": 72723174, "isLive": 0, "oddId": 1972, "odds": "3.40"},
                {"matchId": 72723172, "isLive": 0, "oddId": 1970, "odds": "1.85"},
                {"matchId": 72723172, "isLive": 0, "oddId": 2294, "odds": "1.60"},
            ],
        },
    },
}


router = APIRouter()

# Starlette'in HTTP_422_UNPROCESSABLE_ENTITY sabiti sürümler arasında yeniden
# adlandırıldığı için doğrudan kod kullanılır.
HTTP_422 = 422


def _to_domain(payload: CouponIn) -> tuple[CouponInput, dict[str, str | int]]:
    """API şemasını domain modeline çevirir.

    Maç kimlikleri içeride string olarak normalize edilir; yanıtta çağıranın
    gönderdiği tip (int ya da string) korunsun diye orijinaller de döndürülür.
    """
    original_ids: dict[str, str | int] = {}
    selections = []
    for selection in payload.selections:
        key = str(selection.match_id)
        original_ids.setdefault(key, selection.match_id)
        selections.append(
            SelectionInput(
                match_id=key,
                odd_id=selection.odd_id,
                odd_type_id=selection.odd_type_id or 0,
                outcome=(selection.outcome or "").strip(),
                odds=selection.odds,
                is_live=selection.is_live,
                current_score=selection.current_score,
                special_bet_value=(
                    selection.special_bet_value.strip()
                    if selection.special_bet_value is not None
                    else None
                ),
            )
        )

    coupon = CouponInput(
        selections=tuple(selections),
        coupon_amount=payload.coupon_amount,
        stake_mode=payload.stake_mode,
        system_sizes=tuple(payload.system.sizes) if payload.system else None,
        banker_match_ids=frozenset(str(m) for m in payload.banker_match_ids),
        bonus_multiplier=payload.bonus_multiplier,
        max_payout_cap=payload.max_payout_cap,
        currency=payload.currency.upper(),
    )
    return coupon, original_ids


def _to_response(result: MaxGainResult, original_ids: dict[str, str | int]) -> MaxGainOut:
    return MaxGainOut(
        currency=result.currency,
        stake={
            "total": result.total_stake,
            "per_line": result.stake_per_line,
            "line_count": result.line_count,
        },
        max_gain=result.max_gain,
        net_profit=result.net_profit,
        max_single_line_gain=result.max_single_line_gain,
        effective_multiplier=result.effective_multiplier,
        capped=result.capped,
        matches=[
            {
                "match_id": original_ids.get(match.match_id, match.match_id),
                "banker": match.banker,
                "selection_count": match.selection_count,
                "weight": match.weight,
                "groups": [
                    {
                        "group": group.group,
                        "odds_sum": group.odds_sum,
                        "combined": group.combined,
                        "winning_selections": [w.__dict__ for w in group.winning_selections],
                        "scoreline": (
                            {to_camel(key): value for key, value in group.scoreline.items()}
                            if group.scoreline
                            else None
                        ),
                    }
                    for group in match.groups
                ],
            }
            for match in result.matches
        ],
        breakdown=[item.__dict__ for item in result.breakdown],
        warnings=list(result.warnings),
    )


def _compute(payload: CouponIn) -> MaxGainOut:
    coupon, original_ids = _to_domain(payload)
    try:
        result = calculate_max_gain(coupon)
    except CouponError as exc:
        raise HTTPException(HTTP_422, detail=str(exc)) from exc
    return _to_response(result, original_ids)


@router.post(
    "/coupons/max-gain",
    response_model=MaxGainOut,
    summary="Tek bir kuponun max gain'ini hesapla",
    responses={422: {"model": ErrorOut, "description": "Kupon yapısı geçersiz"}},
)
def compute_max_gain(
    payload: Annotated[CouponIn, Body(openapi_examples=COUPON_EXAMPLES)],
) -> MaxGainOut:
    """Multiway ve/veya sistem kuponunun en iyi senaryodaki toplam ödemesini döner."""
    return _compute(payload)


@router.post(
    "/coupons/max-gain/batch",
    response_model=list[MaxGainOut],
    summary="Birden fazla kuponu tek istekte hesapla",
    responses={422: {"model": ErrorOut}},
)
def compute_max_gain_batch(payload: list[CouponIn]) -> list[MaxGainOut]:
    """Kupon listesini sırayla hesaplar; herhangi biri geçersizse 422 döner."""
    if not payload:
        raise HTTPException(HTTP_422, detail="Kupon listesi boş olamaz.")
    if len(payload) > settings.max_batch_size:
        raise HTTPException(
            HTTP_422,
            detail=f"Tek istekte en fazla {settings.max_batch_size} kupon gönderilebilir.",
        )

    results: list[MaxGainOut] = []
    for index, coupon in enumerate(payload):
        try:
            results.append(_compute(coupon))
        except HTTPException as exc:
            raise HTTPException(HTTP_422, detail=f"Kupon #{index}: {exc.detail}") from exc
    return results


@router.get(
    "/odd-types",
    response_model=OddTypePage,
    summary="oddTypeId kataloğu (pre + live)",
    tags=["katalog"],
)
def odd_types(
    is_live: Annotated[int | None, Query(ge=0, le=1, alias="isLive")] = None,
    q: Annotated[str | None, Query(max_length=100, description="Ada göre arama")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> OddTypePage:
    """Hangi ``oddTypeId``'nin hangi piyasaya karşılık geldiğini listeler.

    Pre-match ve live katalogları ayrı id uzaylarıdır; ``isLive`` ile süzülür.
    ``mapped: false`` olan id'lerin adı bilinir ama anlamı eşlenmemiştir: aynı
    id'nin seçimleri dışlayıcı, farklı id'ler bağımsız kabul edilir ve yanıtın
    ``warnings`` alanında bildirilir.
    """
    return OddTypePage(
        total=catalog_size(is_live, q),
        limit=limit,
        offset=offset,
        items=[OddTypeOut(**row) for row in catalog(is_live, q, limit, offset)],
    )


@router.get("/health", summary="Sağlık kontrolü", tags=["ops"])
def health() -> dict[str, str]:
    return {"status": "ok", "version": settings.version}
