# Multiway MaxGain API

Multiway ve sistem kuponları için **maksimum kazanç (max gain)** hesaplayan REST API.

Kupon bilgisi (event'ler, işaretlenen seçimler, oranlar, sistem tanımı ve stake) gönderilir;
servis en iyi senaryoda oluşacak toplam ödemeyi döner.

---

## Kupon modeli

| Kavram | Anlamı |
|---|---|
| **Event** | Bir maç / karşılaşma. |
| **Selection** | Event için işaretlenen sonuç (`1`, `X`, `over_2_5`, …). Aynı event'in seçimleri **birbirini dışlar** — yalnızca biri gerçekleşir. |
| **Multiway** | Bir event için birden fazla seçim işaretlenmesi. Kupon, bu seçimlerin kombinasyonları kadar satıra açılır. |
| **Sistem (`k`)** | Banko olmayan event'lerden `k`'lı alt kümeler alınarak satır üretilir. `[2,3]` gibi birden fazla boyut aynı anda verilebilir. |
| **Banko** | `banker: true` olan event her satırda zorunlu olarak yer alır; sistem yalnızca kalanlara uygulanır. |
| **Line (satır/way)** | Fiilen oynanan tek bir kombine. Toplam stake satırlara bölünür (`stake_mode: "total"`) ya da her satıra ayrı yatırılır (`stake_mode: "per_line"`). |

### "Max gain" tanımı

Her event'te **tam olarak bir** sonuç gerçekleşir. Max gain, bu gerçekleşme senaryoları
arasından **kazanan tüm satırların toplam ödemesini** en büyük yapanıdır.

Sistem kuponlarında aynı anda birden fazla satır tutabildiği için bu, "en iyi tek satır"dan
farklıdır — o değer de bilgi amacıyla `max_single_line_gain` alanında döner.

---

## Nasıl hesaplanıyor

Naif yaklaşım tüm satırları ve tüm sonuç senaryolarını üretmeyi gerektirir; bu üstel karmaşıklıktır.
Servis bunun yerine iki gözleme dayanan kapalı forma indirger:

1. **En iyi senaryoda her event'te en yüksek oranlı seçim gerçekleşir.** Bir event'te hangi seçimin
   tuttuğu, o event'i içeren satırların yalnızca ödemesini etkiler — başka satırları geçersiz kılmaz.
   Oranlar pozitif olduğundan her event'te maksimumu seçmek toplamı zayıf anlamda domine eder.

2. **Realizasyon sabitlendiğinde her `k`'lı alt kümeden tam olarak bir satır kazanır** ve ödemesi o
   alt kümedeki oranların çarpımıdır. Kazanan satırların toplamı böylece oranların
   **elementer simetrik polinomu** `e_k`'ya eşitlenir:

```
max_gain     = satır_stake × (Π banko oranları) × Σ_k e_k(en yüksek oranlar)
satır_sayısı =               (Π banko seçim sayıları) × Σ_k e_k(seçim sayıları)
```

`e_k` standart DP ile **O(M²)** hesaplanır. 20 bacaklı bir 3/20 sistem (1140 satır) mikrosaniyeler
mertebesinde çözülür; 50 bacaklı kuponlar bile sorun değildir.

Doğruluk, `tests/reference.py` içindeki satırları tek tek üreten kaba kuvvet uygulamasıyla
120 rastgele kupon üzerinde birebir karşılaştırılarak doğrulanır.

Tüm para aritmetiği `Decimal` ile (60 hane hassasiyet) yapılır; ödemeler 2 ondalığa
**aşağı yuvarlanır** (`ROUND_DOWN`). `stake.per_line` ödenen bir tutar değil, toplam stake'in
satırlara bölünmüş hâlidir — milyonluk sistemlerde kuruşun altına indiği için 6 ondalıkla döner.

---

## Kurulum

```bash
uv venv --python 3.12
uv pip install -e ".[dev]"
```

## Çalıştırma

```bash
uv run uvicorn app.main:app --reload
```

- Swagger UI: <http://127.0.0.1:8000/docs>
- OpenAPI şeması: <http://127.0.0.1:8000/openapi.json>

## Test

```bash
uv run pytest -q
```

---

## Uç noktalar

| Method | Yol | Açıklama |
|---|---|---|
| `POST` | `/api/v1/coupons/max-gain` | Tek kupon hesaplar |
| `POST` | `/api/v1/coupons/max-gain/batch` | En fazla 100 kuponu tek istekte hesaplar |
| `GET` | `/api/v1/health` | Sağlık kontrolü |

### Örnek istek

`b1` banko, `m1` multiway (iki seçim), kalanlar tekli — 2'li sistem:

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/coupons/max-gain \
  -H 'Content-Type: application/json' \
  -d '{
    "stake": "10.00",
    "stake_mode": "per_line",
    "currency": "TRY",
    "system": { "sizes": [2] },
    "events": [
      { "id": "b1", "name": "Real - Barca", "banker": true,
        "selections": [ { "id": "1", "odds": "1.50" } ] },
      { "id": "m1", "name": "GS - FB",
        "selections": [ { "id": "1", "odds": "2.00" }, { "id": "X", "odds": "3.50" } ] },
      { "id": "m2", "selections": [ { "id": "2", "odds": "4.00" } ] },
      { "id": "m3", "selections": [ { "id": "1", "odds": "1.80" } ] }
    ]
  }'
```

### Örnek yanıt

```json
{
  "currency": "TRY",
  "stake": { "total": "50.00", "per_line": "10.000000", "line_count": 5 },
  "max_gain": "412.50",
  "net_profit": "362.50",
  "max_single_line_gain": "210.00",
  "effective_multiplier": "8.2500",
  "capped": false,
  "best_scenario": [
    { "event_id": "b1", "event_name": "Real - Barca", "selection_id": "1",
      "selection_name": null, "odds": "1.50", "banker": true },
    { "event_id": "m1", "event_name": "GS - FB", "selection_id": "X",
      "selection_name": null, "odds": "3.50", "banker": false },
    { "event_id": "m2", "event_name": null, "selection_id": "2",
      "selection_name": null, "odds": "4.00", "banker": false },
    { "event_id": "m3", "event_name": null, "selection_id": "1",
      "selection_name": null, "odds": "1.80", "banker": false }
  ],
  "breakdown": [ { "system_size": 2, "line_count": 5, "gross_gain": "412.50" } ],
  "warnings": []
}
```

`m1` için `X` seçilmesinin nedeni oranının daha yüksek olmasıdır (3.50 > 2.00).
Beş satırın üçü (banko + 2'li alt kümeler) en iyi senaryoda birlikte kazanır:
`10 × 1.50 × (3.50×4.00 + 3.50×1.80 + 4.00×1.80) = 412.50`.

---

## İstek alanları

| Alan | Tip | Zorunlu | Açıklama |
|---|---|---|---|
| `events[]` | dizi | ✅ | 1–50 event |
| `events[].id` | string | ✅ | Kupon içinde tekil |
| `events[].selections[]` | dizi | ✅ | Event başına 1–20 seçim, id'leri tekil |
| `events[].selections[].odds` | decimal | ✅ | `> 1.00` |
| `events[].banker` | bool | — | Varsayılan `false` |
| `stake` | decimal | ✅ | `> 0` |
| `stake_mode` | `total` \| `per_line` | — | Varsayılan `total` |
| `system.sizes[]` | int dizisi | — | Verilmezse tam kombine varsayılır |
| `bonus_multiplier` | decimal | — | Ör. `1.10` → %10 bonus |
| `max_payout_cap` | decimal | — | Aşılırsa ödeme kırpılır, `capped: true` döner |
| `currency` | ISO-4217 | — | Varsayılan `TRY` |

Geçersiz kuponlar `422` ile ve açıklayıcı bir `detail` mesajıyla reddedilir.

## Yapılandırma

Ortam değişkenleri `MAXGAIN_` önekiyle okunur (`.env` dosyası da desteklenir):

| Değişken | Varsayılan |
|---|---|
| `MAXGAIN_CORS_ORIGINS` | `["*"]` |
| `MAXGAIN_MAX_BATCH_SIZE` | `100` |
| `MAXGAIN_ROOT_PATH` | `""` |
