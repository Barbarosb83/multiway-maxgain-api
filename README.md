# Multiway MaxGain API

Multiway ve sistem kuponları için **maksimum kazanç (max gain)** hesaplayan REST API.

Kupona ait seçimler (maç kimliği, oddType kimliği, outcome, specialBetValue, oran) ve kupon tutarı
gönderilir; servis en iyi senaryoda oluşacak toplam ödemeyi döner. Tek spora bağlı değildir.

---

## Girdi modeli

Kupon, düz bir **seçim listesi**dir. Her seçim şunları taşır:

| Alan | Açıklama |
|---|---|
| `matchId` | Maç kimliği. Aynı maça **birden fazla** seçim gelebilir — "multiway". |
| `isLive` | Event pre-match ise `0`, live ise `1`. Kimlikler bu bayrağa göre ilgili katalogda aranır. |
| `oddId` | **Seçimin tekil kimliği.** Verildiğinde `oddTypeId` ve `outcome` katalogdan doldurulur. |
| `specialBetValue` | Piyasanın eşiği. Alt/Üst için `"2.5"`, handikap için `"0:1"` ya da `"-1.5"`. Gerektiren piyasalarda zorunlu. |
| `odds` | Ondalık oran (`> 1.00`) |
| `oddTypeId`, `outcome` | `oddId` yoksa zorunlu; varsa yok sayılır (tutarsızlık uyarı olarak bildirilir). |

### Neden `oddId`

Outcome adları sağlayıcıda dile göre değişebilir (`Üst`, `Over`, `Über`) ve kodlamalar
piyasadan piyasaya farklıdır — İY/MS aynı sağlayıcıda hem `1/X`, hem `1X`, hem `HD` biçiminde
geçiyor. `oddId` bunların hepsini tek bir tamsayıya indirger:

```
oddId 1571  ->  oddType 1565 ("3way"),          outcome "1"
oddId 2307  ->  oddType 1481 ("Double Chance"), outcome "1X"
oddId   80  ->  live oddType 24,                outcome "1X"
```

Servis kanonik adı katalogdan okur; gövdedeki `outcome` alanı yalnızca `oddId` yokken kullanılır.

Kupon düzeyinde: `couponAmount`, isteğe bağlı `system`, `bankerMatchIds`, `bonusMultiplier`,
`maxPayoutCap`, `currency`.

### İki ayrı id uzayı

Pre-match ve live katalogları **bağımsız numaralandırılmıştır** ve örtüşür. Aynı sayı iki
katalogda tamamen farklı piyasalar demektir:

| | `oddTypeId` 24 | `oddTypeId` 1565 |
|---|---|---|
| `isLive: 0` (pre) | *(yok)* | `3way` → Maç Sonucu |
| `isLive: 1` (live) | `Double Chance (ALL)` | *(yok)* |

Bu yüzden bir oddType daima `(isLive, oddTypeId)` çiftiyle çözümlenir.

---

## Maç ağırlığı: hangi seçimler birlikte kazanır?

Aynı maçtaki iki seçimin birlikte tutup tutamayacağını belirleyen şey oddType'larının farklı
olması **değil**, aynı anda gerçekleşebilir olmalarıdır:

| Seçimler | Birlikte tutar mı? | Sonuç |
|---|---|---|
| `1X2 "1"` + `1X2 "X"` | ✗ ev kazanır **ve** berabere olamaz | **max** |
| `1X2 "1"` + `ÇŞ "1X"` | ✓ ev kazanırsa ikisi de tutar | **toplam** |
| `1X2 "1"` + `ÇŞ "X2"` | ✗ kesişimleri boş | **max** |
| `Alt 0.5` + `Üst 2.5` | ✗ toplam hem 0 hem 3+ olamaz | **max** |
| `Üst 0.5` + `Üst 2.5` | ✓ toplam 3+ ise ikisi de tutar | **toplam** |
| `1X2 "2"` + `Alt 0.5` | ✗ deplasman kazanırsa en az 1 gol var | **max** |

Dolayısıyla maçın ağırlığı, *birlikte gerçekleşebilen* seçim alt kümeleri arasında oran toplamı
en yüksek olanıdır:

```
w(maç) = max { Σ odds(S) : S seçim alt kümesi, S'nin ortak gerçekleşme senaryosu var }
```

Bu, "aynı oddType → max, farklı oddType → topla" kuralının doğru genellemesidir: ilk iki satırda
onunla aynı sonucu verir, geri kalanlarda onun fazla hesapladığı durumları düzeltir.

### Uyumluluk nasıl biliniyor

Tüm piyasalar ortak bir skor uzayında modellenir:

```
Atom = (İY_ev, İY_dep, MS_ev, MS_dep)      # İY ≤ MS kısıtıyla
```

Her `(oddTypeId, outcome, specialBetValue)` üçlüsü, kazandığı atomların bit maskesine çevrilir
(önbelleklenir). İki seçim, maskelerinin kesişimi boş değilse uyumludur. İlk yarı ve maç sonu
skorlarının **aynı** atomda tutulması sayesinde `"İY 2-0"` ile `"MS 1.5 Alt"` gibi çelişkiler de
kendiliğinden elenir.

**Uzay maça göre uyarlanır.** Skor tavanı sabit değildir; o maçtaki seçimlerin eşiklerinden
türetilir — futbolda `2.5` için 4'lük tavan yeter, basketbolda `220.5` için ~222 gerekir:

| Uzay | Atomlar | Ne zaman |
|---|---|---|
| `FLAT` | `(0, 0, ev, dep)` | Grup tek periyoda ait; büyük tavanlara izin verir (basketbol, kriket) |
| `HALF` | `(ev, dep, ev, dep)` | Yalnızca ilk yarı piyasaları |
| `JOINT` | dört boyutlu | Periyotlar karışıyor; ilk yarı + maç sonu çelişkilerini yakalar |

Ortak uzay atom sınırını (65k) aşarsa hesap periyotlara bölünür ve bu `warnings` ile bildirilir;
periyotlar arası çelişki tespiti kaybolur ama sonuç asla düşmez.

En iyi alt küme, budamalı bir derinlik-öncelikli aramayla bulunur: kalan oranların toplamı mevcut
en iyiyi geçemiyorsa dal kesilir. Yanıt, seçilen alt kümeyi **ve onu gerçekleyen örnek skoru** döner.

---

## oddType kataloğu

Dört katalog da repoda tutulur:

| Dosya | İçerik |
|---|---|
| [`data/odd_types_pre.csv`](data/odd_types_pre.csv) | 559 piyasa, id 1456–2025 |
| [`data/odd_types_live.csv`](data/odd_types_live.csv) | 867 piyasa, id 1–867 |
| [`data/outcomes_pre.csv`](data/outcomes_pre.csv) | `oddTypeId, oddId, outcome` |
| [`data/outcomes_live.csv`](data/outcomes_live.csv) | `oddTypeId, oddId, outcome` |

`GET /api/v1/odd-types` piyasaları listeler (`isLive`, `q`, `limit`, `offset` ile süzülür).
**1426 piyasanın ve 8965 oddId'nin tamamı yüklenir; 158 piyasanın anlamı eşlenmiştir.**

### Eşlemeler veriyle doğrulanır

Bir piyasa adı eşlense bile, o piyasanın **gerçek outcome kümesi** tanımla bağdaşmıyorsa eşleme
kabul edilmez. Yanlış bir eşleme çelişen seçimleri sessizce uyumlu gösterip max gain'i şişirir —
sessiz yanlış, hatadan kötüdür. Bu kontrol gözle bulunamayacak üç hatayı yakaladı:

| oddType | Sanılan | Gerçek outcome kümesi | Doğrusu |
|---|---|---|---|
| `1519 Total Goals` | Alt/Üst | `0-1 goals`, `2-3 goals`, `4-5 goals`, `6+` | aralık |
| `1628 1st Half - Total Goals` | Alt/Üst | `0`, `1`, `2+` | tam sayı |
| `1487 Goals Home` | Alt/Üst | `0`, `1`, `2`, `3+` | tam sayı |

Doğrulanamayan iki id bilerek eşlenmemiştir (`KNOWN_UNMAPPABLE`) ve bir test listenin bundan
ibaret kaldığını sabitler; yeni bir uyumsuzluk çıkarsa CI kırılır.

`Others` / `other` / `C` gibi toplayıcı outcome'lar, kardeş outcome'ların **tümleyeni** olarak
modellenir: `Doğru Skor 1:0` ile `Others` böylece doğru biçimde birbirini dışlar.

### Eşleme neden ad üzerinden

Katalogda aynı ad birden çok id'de tekrar eder (farklı sporlar için ayrı id'ler: `Handicap` 6,
`3way` 5 kez). Bu yüzden eşleme id yerine **ad** üzerinden kurulur
([`app/services/odd_types.py`](app/services/odd_types.py)); yeni bir ad eklemek tek satırdır.

Sporlar arası karışma ağırlığı bozmaz: aynı `matchId` altındaki seçimler daima aynı spora aittir,
dolayısıyla farklı sporların id'leri asla aynı maçta karşılaşmaz. Birim farkı ise uyarlanabilir
uzayla çözülür — basketbol `Üst 220.5` de futbol `Üst 2.5` de aynı kodla değerlendirilir.

Tanımlı piyasalar ([`app/services/markets.py`](app/services/markets.py)):

`MS_1X2` · `CIFT_SANS` · `DNB` · `IY_1X2` · `IY_CIFT_SANS` · `IY_DNB` · `IY2_1X2` ·
`IY2_CIFT_SANS` · `IY2_DNB` · `ALT_UST` · `ALT_UST_3WAY` · `ALT_UST_EV` · `ALT_UST_DEP` ·
`IY_ALT_UST` · `IY2_ALT_UST` · `HANDIKAP` · `IY_HANDIKAP` · `GOL_SAYISI` · `GOL_SAYISI_EV` ·
`GOL_SAYISI_DEP` · `IY_GOL_SAYISI` · `IY2_GOL_SAYISI` · `IY_GOL_SAYISI_EV` ·
`IY_GOL_SAYISI_DEP` · `KARSILIKLI_GOL` · `DOGRU_SKOR` · `IY_MS` · `TEK_CIFT` · `IY_TEK_CIFT` ·
`TOPLAM_GOL`

> Asya handikabında `0` ve çeyrek çizgilerde iade/yarım kazanç vardır; burada kazanır/kazanmaz
> olarak ele alınır. Bu, max gain'i düşürebilir ama asla şişirmez.

### Eşlenmemiş id'ler reddedilmez

Anlamı eşlenmemiş ya da katalogda hiç olmayan bir id için güvenli geri düşüş uygulanır:
aynı `(isLive, oddTypeId)`'nin farklı outcome'ları dışlayıcı (→ max), farklı id'ler bağımsız
(→ toplam) sayılır ve durum `warnings` alanında bildirilir. Yani her kupon hesaplanır; yalnızca
*farklı* id'lerin birbiriyle çeliştiği durumlar tespit edilemez.

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
25 maçlık bir `[2,3,4]` sistem (**1.089.450 satır**) birkaç milisaniyede döner.

Doğruluk, [`tests/reference.py`](tests/reference.py) içindeki **kaba kuvvet** uygulamasıyla güvence
altına alınır. O uygulama motorun hiçbir kısayolunu paylaşmaz: uyumlu alt kümeleri sonuç uzayının
her atomunu deneyerek bulur, tüm satırları açıkça üretir ve tüm senaryoları dolaşır. 150 rastgele
kupon üzerinde iki sonuç birebir karşılaştırılır.

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
| `GET` | `/api/v1/odd-types` | oddTypeId kataloğu (`isLive`, `q`, `limit`, `offset`) |
| `GET` | `/api/v1/health` | Sağlık kontrolü |

JSON alan adları camelCase'tir; snake_case gövdeler de kabul edilir.

### Örnek istek

`900` banko, `901` multiway (1X2 + Çift Şans, uyumlu), `902` Alt/Üst — 2'li sistem:

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/coupons/max-gain \
  -H 'Content-Type: application/json' \
  -d '{
    "couponAmount": "10.00",
    "stakeMode": "per_line",
    "system": { "sizes": [2] },
    "bankerMatchIds": [900],
    "selections": [
      { "matchId": 900, "isLive": 0, "oddId": 1571, "odds": "1.50" },
      { "matchId": 901, "isLive": 0, "oddId": 1571, "odds": "2.00" },
      { "matchId": 901, "isLive": 0, "oddId": 2307, "odds": "1.30" },
      { "matchId": 902, "isLive": 0, "oddId": 1541, "specialBetValue": "2.5", "odds": "4.00" },
      { "matchId": 903, "isLive": 0, "oddId": 1571, "odds": "1.80" }
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
          "group": "SCORE",
          "oddsSum": "3.30",
          "combined": true,
          "winningSelections": [
            { "oddId": 1571, "oddTypeId": 1565, "oddTypeName": "3way", "isLive": 0,
              "outcome": "1",  "odds": "2.00", "specialBetValue": null },
            { "oddId": 2307, "oddTypeId": 1481, "oddTypeName": "Double Chance", "isLive": 0,
              "outcome": "1X", "odds": "1.30", "specialBetValue": null }
          ],
          "scoreline": { "halfTime": null, "fullTime": "1-0" }
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
| `selections[].isLive` | `0` \| `1` | — | Varsayılan `0` (pre-match) |
| `selections[].oddId` | int | ◐ | Verilirse `oddTypeId`/`outcome` katalogdan doldurulur |
| `selections[].oddTypeId` | int | ◐ | `oddId` yoksa zorunlu |
| `selections[].outcome` | string | ◐ | `oddId` yoksa zorunlu |
| `selections[].specialBetValue` | string | — | Gerektiren piyasalarda zorunlu (`needsSpecialBetValue`) |
| `selections[].odds` | decimal | ✅ | `> 1.00` |
| `couponAmount` | decimal | ✅ | `> 0` |
| `stakeMode` | `total` \| `per_line` | — | Varsayılan `total` |
| `system.sizes[]` | int dizisi | — | Verilmezse tam kombine varsayılır |
| `bankerMatchIds[]` | dizi | — | Her satırda zorunlu yer alacak maçlar |
| `bonusMultiplier` | decimal | — | Ör. `1.10` → %10 bonus |
| `maxPayoutCap` | decimal | — | Aşılırsa ödeme kırpılır, `capped: true` |
| `currency` | ISO-4217 | — | Varsayılan `TRY` |

Her seçim ya `oddId` ile ya da `oddTypeId` + `outcome` ile tanımlanmalıdır.
Aynı `(matchId, isLive, oddId, oddTypeId, outcome, specialBetValue)` altılısı iki kez gönderilemez — ama
`Üst 0.5` ile `Üst 2.5` farklı seçimlerdir ve birlikte oynanabilir. Geçersiz kuponlar `422` ile ve
açıklayıcı bir `detail` mesajıyla reddedilir.

## Yapılandırma

Ortam değişkenleri `MAXGAIN_` önekiyle okunur (`.env` de desteklenir):
`MAXGAIN_CORS_ORIGINS`, `MAXGAIN_MAX_BATCH_SIZE`, `MAXGAIN_ROOT_PATH`.
