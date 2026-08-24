"""HTTP uç noktaları."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.core.config import settings
from app.models.schemas import CouponIn, ErrorOut, MaxGainOut
from app.services.max_gain import (
    CouponError,
    CouponInput,
    EventInput,
    MaxGainResult,
    SelectionInput,
    calculate_max_gain,
)

router = APIRouter()


def _to_domain(payload: CouponIn) -> CouponInput:
    """API şemasını framework'ten bağımsız domain modeline çevirir."""
    return CouponInput(
        events=tuple(
            EventInput(
                id=event.id,
                name=event.name,
                banker=event.banker,
                selections=tuple(
                    SelectionInput(id=s.id, name=s.name, odds=s.odds) for s in event.selections
                ),
            )
            for event in payload.events
        ),
        stake=payload.stake,
        stake_mode=payload.stake_mode,
        system_sizes=tuple(payload.system.sizes) if payload.system else None,
        bonus_multiplier=payload.bonus_multiplier,
        max_payout_cap=payload.max_payout_cap,
        currency=payload.currency.upper(),
    )


def _to_response(result: MaxGainResult) -> MaxGainOut:
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
        best_scenario=[pick.__dict__ for pick in result.best_scenario],
        breakdown=[item.__dict__ for item in result.breakdown],
        warnings=list(result.warnings),
    )


@router.post(
    "/coupons/max-gain",
    response_model=MaxGainOut,
    summary="Tek bir kuponun max gain'ini hesapla",
    responses={422: {"model": ErrorOut, "description": "Kupon yapısı geçersiz"}},
)
def compute_max_gain(payload: CouponIn) -> MaxGainOut:
    """Multiway ve/veya sistem kuponunun en iyi senaryodaki toplam ödemesini döner."""
    try:
        result = calculate_max_gain(_to_domain(payload))
    except CouponError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return _to_response(result)


@router.post(
    "/coupons/max-gain/batch",
    response_model=list[MaxGainOut],
    summary="Birden fazla kuponu tek istekte hesapla",
    responses={422: {"model": ErrorOut}},
)
def compute_max_gain_batch(payload: list[CouponIn]) -> list[MaxGainOut]:
    """Kupon listesini sırayla hesaplar; herhangi biri geçersizse 422 döner."""
    if not payload:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Kupon listesi boş olamaz."
        )
    if len(payload) > settings.max_batch_size:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Tek istekte en fazla {settings.max_batch_size} kupon gönderilebilir.",
        )

    results: list[MaxGainOut] = []
    for index, coupon in enumerate(payload):
        try:
            results.append(_to_response(calculate_max_gain(_to_domain(coupon))))
        except CouponError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Kupon #{index}: {exc}"
            ) from exc
    return results


@router.get("/health", summary="Sağlık kontrolü", tags=["ops"])
def health() -> dict[str, str]:
    return {"status": "ok", "version": settings.version}
