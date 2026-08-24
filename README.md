# Multiway MaxGain API

Multiway ve sistem kuponları için **maksimum kazanç (max gain)** hesaplayan REST API.

Kupona ait seçimler (maç kimliği, oddType kimliği, outcome, oran) ve kupon tutarı gönderilir;
servis en iyi senaryoda oluşacak toplam ödemeyi döner.

---

## Girdi modeli

Kupon, düz bir **seçim listesi**dir. Her seçim şunları taşır:

| Alan | Açıklama |
|---|---|
| `matchId` | Maç kimliği. Aynı maça **birden fazla** seçim gelebilir — "multiway". |
| `oddTypeId` | Piyasa kimliği. Ör. `1` → Maç Sonucu (1X2), `2` → Çift Şans. Katalog: `GET /api/v1/odd-types` |
| `outcome` | Sonuç kodu. Ör. `"1"`, `"X2"`, `"2.5 Üst"`, `"2-1"` |
| `odds` | Ondalık oran (`> 1.00`) |

Kupon düzeyinde: `couponAmount` (kupon tutarı), isteğe bağlı `system`, `bankerMatchIds`,
`bonusMultiplier`, `maxPayoutCap`, `currency`.

---

## Maç ağırlığı: hangi seçimler birlikte kazanır?

Aynı maçtaki iki seçimin birlikte tutup tutamayacağını belirleyen şey oddType'larının farklı
olması **değil**, aynı anda gerçekleşebilir olmalarıdır:

| Seçimler | Birlikte tutar mı? | Sonuç |
|---|---|---|
| `1X2 "1"` + `1X2 "X"` | ✗ ev kazanır **ve** berabere olamaz | **max** |
| `1X2 "1"` + `ÇŞ "1X"` | ✓ ev kazanırsa ikisi de tutar | **toplam** |
| `1X2 "1"` + `ÇŞ "X2"` | ✗ kesişimleri boş | **max** |
| `1X2 "2"` + `"0.5 Alt"` | ✗ deplasman kazanırsa en az 1 gol var | **max** |

Dolayısıyla maçın ağırlığı, *birlikte gerçekleşebilen* seçim alt kümeleri arasında oran toplamı
en yüksek olanıdır:

```
w(maç) = max { Σ odds(S) : S seçim alt kümesi, S'nin ortak gerçekleşme senaryosu var }
```

Bu, "aynı oddType → max, farklı oddType → topla" kuralının doğru genellemesidir: ilk iki satırda
onunla aynı sonucu verir, son iki satırda ise onun fazla hesapladığı durumu düzeltir.

### Uyumluluk nasıl biliniyor

Gol bazlı tüm piyasalar tek bir ortak sonuç uzayında modellenir:

```
Atom = (İY_ev, İY_dep, MS_ev, MS_dep)      # 1296 atom, skorlar 0..7, İY ≤ MS
```

Her `(oddTypeId, outcome)` ikilisi, kazandığı atomların bir bit maskesine çevrilir (önbelleklenir).
İki seçim, maskelerinin kesişimi boş değilse uyumludur. İlk yarı ve maç sonu skorlarının **aynı**
atomda tutulması sayesinde `"İY 2-0"` ile `"MS 1.5 Alt"` gibi çelişkiler de kendiliğinden elenir.

En iyi alt küme, budamalı bir derinlik-öncelikli aramayla bulunur: kalan oranların toplamı mevcut
en iyiyi geçemiyorsa dal kesilir. Yanıt, seçilen alt kümeyi **ve onu gerçekleyen örnek skoru** döner.

---

## oddType kataloğu

`GET /api/v1/odd-types` hangi id'nin hangi piyasa olduğunu listeler. Katalog
[`app/services/odd_types.py`](app/services/odd_types.py) içindedir ve yeni id eklemek tek satırdır:

```python
ODD_TYPE_MARKET: dict[int, str] = {
    1: "MS_1X2",      # Maç Sonucu (1X2)
    2: "CIFT_SANS",   # Çift Şans
    3: "ALT_UST",     # <- yeni id böyle eklenir
}
```

Piyasaların *anlamı* [`app/services/markets.py`](app/services/markets.py) içinde hazırdır.
Tanımlı piyasalar:

`MS_1X2` · `CIFT_SANS` · `IY_1X2` · `IY_CIFT_SANS` · `ALT_UST` · `IY_ALT_UST` ·
`KARSILIKLI_GOL` · `DOGRU_SKOR` · `IY_MS` · `TEK_CIFT` · `TOPLAM_GOL` · `HANDIKAP`

> **Katalogda yalnızca teyitli id'ler var (1 ve 2).** Yanlış bir id eşlemesi, çelişen seçimlerin
> uyumlu sayılmasına yol açacağı için tahmine dayalı eşleme eklenmedi.

**Katalogda olmayan id gelirse istek reddedilmez.** Güvenli geri düşüş uygulanır:
aynı `oddTypeId`'nin farklı outcome'ları dışlayıcı (→ max), farklı id'ler bağımsız (→ toplam)
sayılır ve durum `warnings` alanında bildirilir.

---

## Kupon toplamı nasıl hesaplanıyor

Her satır (line/way), dahil olduğu her maçtan **tam olarak bir** seçim alır. Sistem tanımı, banko
olmayan maçlardan `k`'lı alt kümeler üretir; banko maçlar her satırda yer alır.

Naif yaklaşım tüm satırları ve tüm sonuç senaryolarını üretmeyi gerektirir — üstel. Ancak
realizasyon sabitlendiğinde `k` boyutlu her alt kümeden kazanan satırların toplamı, maç
ağırlıklarının **elementer simetrik polinomu** `e_k`'ya eşitlenir:

```
max_gain     = satır_stake × (Π banko ağırlıkları)     × Σ_k e_k(ağırlıklar)
satır_sayısı =               (Π banko seçim sayıları)  × Σ_k e_k(seçim sayıları)
```

`e_k` standart DP ile **O(M²)** hesaplanır; satırlar hiçbir zaman tek tek üretilmez.
25 maçlık bir `[2,3,4]` sistem (**1.089.450 satır**) ~1.5 ms'de döner.

Doğruluk, [`tests/reference.py`](tests/reference.py) içindeki **kaba kuvvet** uygulamasıyla
güvence altına alınır: o uygulama tüm satırları açıkça üretir, maçların tüm sonuç senaryolarını
dolaşır ve yalnızca tanımı uygular. 150 rastgele kupon üzerinde iki sonuç birebir karşılaştırılır.

Tüm para aritmetiği `Decimal` ile (60 hane) yapılır; ödemeler 2 ondalığa **aşağı yuvarlanır**
(`ROUND_DOWN`). `stake.perLine` ödenen bir tutar değil, kupon tutarının satırlara bölünmüş
hâlidir — milyonluk sistemlerde kuruşun altına indiği için 6 ondalıkla döner.

---

## Kurulum ve çalıştırma

```bash
uv venv --python 3.12
uv pip install -e ".[dev]"
uv run uvicorn app.main:app --reload
```

- Swagger UI: <http://127.0.0.1:8000/docs>
- Testler: `uv run pytest -q`

---

## Uç noktalar

| Method | Yol | Açıklama |
|---|---|---|
| `POST` | `/api/v1/coupons/max-gain` | Tek kupon hesaplar |
| `POST` | `/api/v1/coupons/max-gain/batch` | En fazla 100 kuponu tek istekte hesaplar |
| `GET` | `/api/v1/odd-types` | oddTypeId kataloğu |
| `GET` | `/api/v1/health` | Sağlık kontrolü |

JSON alan adları camelCase'tir; snake_case gövdeler de kabul edilir.

### Örnek istek

`900` banko, `901` multiway (1X2 + Çift Şans, uyumlu), 2'li sistem:

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/coupons/max-gain \
  -H 'Content-Type: application/json' \
  -d '{
    "couponAmount": "10.00",
    "stakeMode": "per_line",
    "system": { "sizes": [2] },
    "bankerMatchIds": [900],
    "selections": [
      { "matchId": 900, "oddTypeId": 1, "outcome": "1",  "odds": "1.50" },
      { "matchId": 901, "oddTypeId": 1, "outcome": "1",  "odds": "2.00" },
      { "matchId": 901, "oddTypeId": 2, "outcome": "1X", "odds": "1.30" },
      { "matchId": 902, "oddTypeId": 1, "outcome": "2",  "odds": "4.00" },
      { "matchId": 903, "oddTypeId": 1, "outcome": "1",  "odds": "1.80" }
    ]
  }'
```

### Örnek yanıt (kısaltılmış)

```json
{
  "currency": "TRY",
  "stake": { "total": "50.00", "perLine": "10.000000", "lineCount": 5 },
  "maxGain": "395.10",
  "netProfit": "345.10",
  "maxSingleLineGain": "120.00",
  "effectiveMultiplier": "7.9020",
  "capped": false,
  "matches": [
    {
      "matchId": 901,
      "banker": false,
      "selectionCount": 2,
      "weight": "3.30",
      "groups": [
        {
          "group": "GOALS",
          "oddsSum": "3.30",
          "combined": true,
          "winningSelections": [
            { "oddTypeId": 1, "oddTypeName": "Maç Sonucu (1X2)", "outcome": "1",  "odds": "2.00" },
            { "oddTypeId": 2, "oddTypeName": "Çift Şans",        "outcome": "1X", "odds": "1.30" }
          ],
          "scoreline": { "halfTime": "0-0", "fullTime": "1-0" }
        }
      ]
    }
  ],
  "breakdown": [ { "systemSize": 2, "lineCount": 5, "grossGain": "395.10" } ],
  "warnings": []
}
```

`901` için ağırlık `2.00 + 1.30 = 3.30`: ev sahibi kazandığında hem `1X2 "1"` hem `ÇŞ "1X"` tutar,
`combined: true` bunu belirtir. `scoreline` o senaryoyu gerçekleyen örnek skordur.

Beş satırın üçü en iyi senaryoda birlikte kazanır:
`10 × 1.50 × (3.30×4.00 + 3.30×1.80 + 4.00×1.80) = 395.10`.

---

## İstek alanları

| Alan | Tip | Zorunlu | Açıklama |
|---|---|---|---|
| `selections[]` | dizi | ✅ | En az 1 seçim; en fazla 50 maç, maç başına 12 seçim |
| `selections[].matchId` | int \| string | ✅ | Yanıtta gönderilen tiple aynen döner |
| `selections[].oddTypeId` | int | ✅ | Katalogda yoksa geri düşüş + uyarı |
| `selections[].outcome` | string | ✅ | Piyasaya göre çözümlenir |
| `selections[].odds` | decimal | ✅ | `> 1.00` |
| `couponAmount` | decimal | ✅ | `> 0` |
| `stakeMode` | `total` \| `per_line` | — | Varsayılan `total` |
| `system.sizes[]` | int dizisi | — | Verilmezse tam kombine varsayılır |
| `bankerMatchIds[]` | dizi | — | Her satırda zorunlu yer alacak maçlar |
| `bonusMultiplier` | decimal | — | Ör. `1.10` → %10 bonus |
| `maxPayoutCap` | decimal | — | Aşılırsa ödeme kırpılır, `capped: true` |
| `currency` | ISO-4217 | — | Varsayılan `TRY` |

Aynı `(matchId, oddTypeId, outcome)` üçlüsü iki kez gönderilemez. Geçersiz kuponlar `422` ile ve
açıklayıcı bir `detail` mesajıyla reddedilir.

## Yapılandırma

Ortam değişkenleri `MAXGAIN_` önekiyle okunur (`.env` de desteklenir):
`MAXGAIN_CORS_ORIGINS`, `MAXGAIN_MAX_BATCH_SIZE`, `MAXGAIN_ROOT_PATH`.
