"""FastAPI uygulama girişi."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.core.config import settings

app = FastAPI(
    title=settings.app_name,
    version=settings.version,
    root_path=settings.root_path,
    summary="Multiway ve sistem kuponları için maksimum kazanç hesaplama servisi",
    description=(
        "Kupon bilgisi (event'ler, seçimler, oranlar, sistem tanımı ve stake) gönderilir; "
        "servis en iyi senaryoda oluşacak toplam ödemeyi (max gain) döner.\n\n"
        "Satırlar tek tek üretilmez; hesap elementer simetrik polinomlara indirgenerek "
        "O(M^2) sürede yapılır."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api/v1")


@app.get("/", include_in_schema=False)
def root() -> dict[str, str]:
    return {"service": settings.app_name, "docs": "/docs", "health": "/api/v1/health"}
