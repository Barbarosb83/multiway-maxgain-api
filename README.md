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
| `currentScore` | Maçın o anki skoru (`"0:2"`). Canlı seçimlerde gönderilmeli; pre-match'te `"0:0"`. |
| `specialBetValue` | Piyasaya göre değişir: Alt/Üst'te eşik (`"2.5"`), handikapta fark (`"0:1"`, `"-1.5"`), canlı "maçın kalanı"nda **bahis anındaki skor** (`"0:0"`). Gerektiren piyasalarda zorunlu. |
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

Servis kanonik adı katalogdan okur. **Üretimde beklenen yol budur**; gövdedeki `outcome` alanı
yalnızca `oddId` yokken devreye girer.

### Çok dilli outcome kodları (emniyet ağı)

`oddId` gelmediğinde outcome adı doğrudan çözümlenir ve sağlayıcı bunu kendi dilinde gönderebilir.
Gerçek kuponlarda aynı piyasanın (`1467`, karşılıklı gol) şu kodlarla geldiği görüldü:

| Anlam | Tanınan kodlar |
|---|---|
| Karşılıklı gol var | `Yes` · `Y` · `J` · `Ja` · `Var` · `Evet` · `E` · `goal` · `Si` · `Oui` |
| Karşılıklı gol yok | `No` · `N` · `Nein` · `Yok` · `Hayır` · `H` · `nogoal` · `Non` |
| Üst | `Over` · `Üst` · `Über` · `O` |
| Alt | `Under` · `Alt` · `Unter` · `U` |
| Tek / Çift | `Odd`/`Ungerade` · `Even`/`Gerade` |

Sonuç kodları (`1`, `X`, `2`, `1X`, `12`, `X2`) büyük/küçük harf duyarsızdır — katalog `X`,
kupon gövdesi `x` gönderebiliyor.

Bir test, gerçek kuponları iki yolla da hesaplayıp sonuçların birebir aynı çıktığını doğrular;
böylece çok dilli çözümlemenin katalogla tutarlı kaldığı garanti altına alınır.

Kupon düzeyinde: `couponAmount`, isteğe bağlı `system`, `bankerMatchIds`, `bonusMultiplier`,
`maxPayoutCap`, `currency`.

### Sağlayıcı gövdesinden eşleme

Gerçek kupon gövdesindeki alanların karşılıkları:

| Gövde alanı | API alanı | Not |
|---|---|---|
| `MatchId` | `matchId` | Canlı maçlarda **negatif** olabilir |
| `BetType` | `isLive` | `0` = pre-match, `1` = canlı |
| `OddsTypeId` | `oddTypeId` | `isLive`'a göre ilgili katalogda aranır |
| `OutCome` | `outcome` | `oddId` yoksa kullanılır; çok dilli tanınır |
| `SpecialBetValue` | `specialBetValue` | Boş string (`""`) yokluk sayılır |
| *(skor)* | `currentScore` | Gövdede yok; canlı maçlarda ayrıca gönderilmeli |
| `OddValue1` | `odds` | **Güncel** oran. Tam sayı gelebilir, en az iki ondalığa tamamlanır; dört ondalık (`1.6667`) korunur |
| `Banko` | `bankerMatchIds` | `true` olan maçların kimlikleri |

Kupon tutarı gövdede yer almaz; `couponAmount` olarak ayrıca gönderilir.

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
| `Kalan "1"` (1:0'dan) + `1X2 "2"` | ✗ ev kalanı da alırsa deplasman kazanamaz | **max** |
| `"Over and home"` + `1X2 "2"` | ✗ kombine piyasa ev galibiyeti şart koşuyor | **max** |
| `Ç1 Handikap 0:25` + `Ç1 Alt 20.5` | ✗ 26+ fark ile 20'den az toplam olmaz | **max** |

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

**Uzay maça göre uyarlanır.** Hem yerleşim hem skor tavanı, o maçtaki seçimlerden türetilir —
futbolda `2.5` için 4'lük tavan yeter, basketbolda `220.5` için ~222 gerekir:

| Yerleşim | Atom | Ne zaman |
|---|---|---|
| `MATCH` | `(ev, dep)` | Gruptaki seçimler **tek periyoda** ait — maç sonu, ilk yarı, 2. çeyrek, 3. periyot fark etmez. İki boyutlu olduğu için büyük skorlara yer kalır. |
| `HALVES` | `(İY_ev, İY_dep, 2Y_ev, 2Y_dep)` | İlk yarı / ikinci yarı / maç sonu birlikte. Maç sonu iki yarının toplamıdır; `"İY 2-0"` ile `"MS 1.5 Alt"` çelişkisi böyle yakalanır. |

Yüklemler atomun ham indekslerine değil, periyot skorlarına bakar. Bu sayede tek bir "maç sonucu"
tanımı maç sonu, ilk yarı, çeyrek ve periyot varyantlarında yeniden kullanılır — katalogdaki
`1st Quarter - Points Spread` ile `1st Quarter - Total Spread` aynı kısıt grubuna düşer ve
çelişirlerse yakalanır.

Ortak yerleşim atom sınırını (65k) aşarsa ya da karışan periyotlar yarılarla ifade edilemiyorsa
(çeyrek + maç sonu gibi) hesap periyotlara bölünür ve bu `warnings` ile bildirilir; periyotlar
arası çelişki tespiti kaybolur ama sonuç asla düşmez.

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
**1426 piyasanın ve 8965 oddId'nin tamamı yüklenir; 289 piyasanın anlamı eşlenmiştir.**

### Eşlemeler veriyle doğrulanır

Bir piyasa adı eşlense bile, o piyasanın **gerçek outcome kümesi** tanımla bağdaşmıyorsa eşleme
kabul edilmez. Yanlış bir eşleme çelişen seçimleri sessizce uyumlu gösterip max gain'i şişirir —
sessiz yanlış, hatadan kötüdür. Bu kontrol gözle bulunamayacak üç hatayı yakaladı:

| oddType | Sanılan | Gerçek outcome kümesi | Doğrusu |
|---|---|---|---|
| `1519 Total Goals` | Alt/Üst | `0-1 goals`, `2-3 goals`, `4-5 goals`, `6+` | aralık |
| `1628 1st Half - Total Goals` | Alt/Üst | `0`, `1`, `2+` | tam sayı |
| `1487 Goals Home` | Alt/Üst | `0`, `1`, `2`, `3+` | tam sayı |

Aynı kontrol, adın tek başına yetmediği durumları da ayıklar. Live katalogunda iki piyasa da
`Winner` adını taşır:

| id | Outcome kümesi | Sonuç |
|---|---|---|
| `live 708` | `1`, `x`, `2` | Maç sonucu — eşlendi (live'ın ana piyasası) |
| `live 180` | `competitor_1` … `competitor_14` | Çok yarışmacılı outright — eşlenmedi |

Doğrulanamayan iki id bilerek eşlenmemiştir (`KNOWN_UNMAPPABLE`) ve bir test listenin bundan
ibaret kaldığını sabitler; yeni bir uyumsuzluk çıkarsa CI kırılır.

Katalogda **hatalı kayıtlar** da var: bazı alt/üst piyasaları `o`/`u` yanında `1` ve `2`
outcome'larını listeliyor (ör. `live 19`). Bunların alt/üst karşılığı yok. Piyasa tanımında
`stray_outcomes` olarak işaretlenirler: eşlemeyi engellemezler, ama böyle bir seçim gerçekten
gelirse yalıtılır ve uyarı verilir — sessizce yanlış yöne çevrilmez.

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
`GOL_SAYISI` · `KOMBINE` · `EV_VEYA_KG` … ve bunların **ilk yarı, ikinci yarı, 1–4. çeyrek,
1–5. periyot** varyantları (`Q1_HANDIKAP`, `P2_ALT_UST`, `IY2_KOMBINE` …) — toplam 174 tanım.

**Kombine piyasalar** iki koşulun kesişimidir ve outcome'ları `"Over and home"`, `"Home / Yes"`,
`"DrawAway / Under"` gibi iki bileşenlidir. Ayrıştırıcı bileşenleri (sonuç, çift şans, alt/üst,
karşılıklı gol) tanır ve sırası önemsizdir; katalog hem `"Over and home"` hem `"away and over"`
biçimini kullanıyor. `Home Or Both Teams To Score` gibi **birleşim** piyasaları da ayrıca modellenir.

Bu sağlayıcıda `Spread` handikap ya da alt/üst demektir (`Points Spread`, `Total Spreads`,
`Goal Spread Main Line`), `AAMS regular time` ise düzenli oyun süresini kapsayan tam maç
piyasasıdır; ikisi de eşlenmiştir.

> Asya handikabında `0` ve çeyrek çizgilerde iade/yarım kazanç vardır; burada kazanır/kazanmaz
> olarak ele alınır. Bu, max gain'i düşürebilir ama asla şişirmez.

**Canlı "maçın kalanı"** (`live 3`) bahsi o anki skordan sonrasına yatırılır: kazanan taraf kalan
sürede daha çok gol atandır. `specialBetValue` bahis anındaki skoru taşır ve maç sonu skorundan
düşülür; maç sonunun anlık skorun altına inemeyeceği de kısıt olarak eklenir. Anlık skor `0:0`
olduğunda piyasa maç sonucuyla aynıya indirgenir.

### Anlık skor: neden ayrıca gerekli

Canlı bir maçta o ana kadar atılan goller, maç sonu skorunun **alt sınırıdır**. Bu bilgi tek bir
piyasadan (`live 3`, "maçın kalanı") gelemez, çünkü o piyasa her kuponda oynanmaz. Bu yüzden skor
seçim düzeyinde `currentScore` olarak alınır ve o maçın tüm seçimlerine kısıt olarak uygulanır.

Gerçek bir kupondan örnek — `Jong Utrecht` maçı **0:2**, kuponda `3.5 Alt` @2.70 ve
`Maç Sonucu "1"` @30.00 var:

| | Maç ağırlığı | Kupon max gain |
|---|---|---|
| Skor gönderilmeden | `2.70 + 30.00 = 32.70` | 3678.75 |
| Skor `0:2` ile | `30.00` | **3375.00** |

`0:2`'den ev sahibinin kazanması için 3+ gol atması gerekir; toplam en az 5 olur ve `3.5 Alt` ile
çelişir. Skor olmadan bu görülemez ve iki seçim toplanır — %9 fazla hesap.

Aynı kısıt, **zaten kaybetmiş** seçimleri de eler: maç 2-0 iken `1.5 Alt` hiçbir senaryoda
kazanamaz, hesaba katılmaz ve `warnings` ile bildirilir. Canlı bir seçim skorsuz gelirse bu da
uyarı olarak döner.

**Sıradaki gol** (`live 11`) bir *sıralama* iddiasıdır; modellenen uzay ise yalnızca skorları
taşır. Bahis, maç sonu skoruna **izdüşürülerek** modellenir — anlık skor `ch:ca` iken:

| Outcome | Kısıt |
|---|---|
| `1` (ev) | `ft_ev ≥ ch+1` |
| `2` (deplasman) | `ft_dep ≥ ca+1` |
| `x` (daha gol yok) | `ft = ch:ca` |

İzdüşüm bir üst kümedir: aynı maçta **tek** sıralama piyasası varken kesindir (o skora götüren bir
gol sırası daima kurulabilir), birden fazlası varsa sonucu bir miktar yüksek tutabilir. Yine de
piyasayı hiç modellememekten **her zaman daha dardır** — yalıtılmış bir seçim koşulsuz toplanırken
izdüşüm çelişenleri eler:

| `0:0` iken | Sonuç |
|---|---|
| `Sıradaki gol "2"` + `Doğru skor 1:0` | çelişki — deplasman hiç gol atmamış |
| `Sıradaki gol "2"` + `Doğru skor 1:1` | uyumlu — deplasman önce atar |
| `Sıradaki gol "x"` + `0.5 Üst` | çelişki — daha gol atılmayacak |

### Yanlış katalog uyarısı

Bir id kendi katalogunda bulunamayıp **diğerinde** bulunursa, bu genellikle `isLive` bayrağının
seçime uymadığı anlamına gelir. Uyarı bunu açıkça söyler:

```
oddType 1839 (live) live katalogunda yok, ancak pre katalogunda '3 Way' olarak var
-- isLive bayrağı seçime uymuyor olabilir
```

Bu, gerçek bir kuponda iki farklı piyasanın (`1X2` ve `NG`) aynı `oddTypeId`'yi taşımasını
yakaladı. Aynı id'yi taşıdıkları için dışlayıcı sayılmışlar ve maç ağırlığı `4.25` yerine `2.25`
çıkmıştı — kupon toplamında 469.27 yerine 248.43.

### Eşlenmemiş id'ler reddedilmez

Anlamı eşlenmemiş ya da katalogda hiç olmayan bir id için güvenli geri düşüş uygulanır:
aynı `(isLive, oddTypeId)`'nin farklı outcome'ları dışlayıcı (→ max), farklı id'ler bağımsız
(→ toplam) sayılır ve durum `warnings` alanında bildirilir.

Eşlenmemiş piyasaların büyük bölümü için bu **zaten doğru davranıştır**: korner, kart, set,
periyot, harita gibi piyasalar maç skorundan farklı bir büyüklüğü ölçer ve gerçekten bağımsızdır.
Eşleme yalnızca *aynı* büyüklüğü ölçen piyasalar arasında fark yaratır — orada da kaçırılan tek
durum, farklı id'lerin birbiriyle çelişmesidir.

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
| `selections[].odds` | decimal | ✅ | `> 1.00`. Seçimin **güncel** oranı; servis oran geçmişi tutmaz |
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
