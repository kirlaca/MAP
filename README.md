# MÁP – állampapír-ajánló

Negyedévente futtatható, helyi Python-program a **Magyar Államkincstárnál** (WebKincstár) vezetett lakossági állampapír-portfólióhoz.

A program beolvassa a WebKincstár portfólió-exportját és letölti az aktuális kínálatot. Vetíti a tartott és a kapható sorozatok kamatát (infláció, DKJ, EURIBOR), majd javaslatot tesz:

- **tartásra vagy cserére**, a csere legjobb időpontjával (most, vagy egy későbbi kamatforduló után);
- a **szabad pénz** és a **lejáró összegek** befektetésére;
- a **cél EUR-arány** tartására, lejárat előtti eladás nélkül.

Minden lépésnél kiírja a **várható nominális többletet, 1 évre vetítve**.

**🌐 Napi kínálat online:** https://kirlaca.github.io/MAP/ – a most kapható állampapírok vetített kamata, és a program riportja egy kitalált minta-portfólióra. Naponta frissül (GitHub Actions).

> ⚠️ Ez modellszámítás, nem befektetési tanácsadás. A vetített kamatok feltevéseken alapulnak. Kötés előtt nézd meg a WebKincstárban az aktuális kínálatot és árfolyamot.

## Fő funkciók

- **Kamatpálya-vetítés típusonként**
  - PMÁP: az előző évi KSH-infláció + prémium.
  - PEMÁP: az előző évi euróövezeti HICP + prémium.
  - BMÁP: a 3 havi DKJ-hozam + prémium.
  - EMÁP: a 3 havi EURIBOR.
  - Fix MÁP és MÁP Plusz: fix, illetve sávos kamat.

  A prémiumot a program a sorozat nyilvános ajánlattételéből (PDF) vagy a kamattörténetből számolja.
- **Csereelemzés:** a tartott papír és a legjobb kapható papír várható kamata, mínusz a kincstári visszaváltási költség (nettó 99%). Ha a folyó periódus kamata jobb, a program kivárja a kamatfordulót.
- **Devizaarány:** a cél EUR-arányt (alapból 15% ± 5 pont) a szabad pénzzel és a lejáró összegekkel tartja. Kiírja a fedezeti EUR/HUF árfolyamot is.
- **Kínálatfigyelés:** jelzi az új és a megszűnt termékeket, sorozatokat. Egy új sorozatot automatikusan bevon a vizsgálatba.
- **Pénzáramlás-naptár** (12 hónap), **lejárati létra**, a **feltevések** listája.
- **Helyi webfelület**, öt füllel:
  - Áttekintés;
  - Feltöltés: az XLS-export feltöltése;
  - Ajánló: futtatás és riportok;
  - Beállítások: a config szerkesztése, ellenőrzéssel és archiválással;
  - Útmutató, technikai résszel.

## Telepítés

Python 3.10 vagy újabb kell.

```bash
git clone https://github.com/kirlaca/MAP.git
cd MAP
pip install -r requirements.txt
```

## Használat

### Webfelület (ajánlott)

```bash
python app/webapp.py        # Windows-on: dupla klikk a start_webapp.bat-ra
```

A böngésző magától megnyílik: http://127.0.0.1:8766

1. **Feltöltés:** WebKincstár → Portfólió → Excel export (`portfolio.xls`). Húzd be, nézd meg az előnézetet, majd *Import mentése*.
2. **Ajánló:** *Ajánló futtatása*. A riport a `private/reports/ajanlo_ÉÉÉÉ-QN.md` fájlba kerül.
3. A javasolt kötéseket kézzel kell beadni a WebKincstárban.

### Parancssor

```bash
python scripts/mak_portfolio_import.py portfolio.xls [--proba]   # → private/holdings.csv, private/cash.csv
python scripts/negyedeves_ajanlo.py [--frissit]                  # → private/reports/ajanlo_ÉÉÉÉ-QN.md
python scripts/allampapir_adatok.py [--frissit]                  # csak az adatletöltés, összefoglalóval
```

### Kipróbálás a minta-portfólióval

A `minta/portfolio_minta.xls` kitalált adatokat tartalmaz, a WebKincstár-export formátumában:

```bash
python scripts/mak_portfolio_import.py minta/portfolio_minta.xls
python scripts/negyedeves_ajanlo.py
```

A webfelületen is feltölthető a Feltöltés fülön.

## Beállítások

`config/strategia.yaml`, a webfelület **Beállítások** fülén is szerkeszthető:

| kulcs | jelentés | alap |
|---|---|---|
| `horizont_ev` | ennyi évre hasonlítja a várható átlagkamatot | 3 |
| `csere.min_elony_pp` | csereküszöb: évi többlet a visszaváltási költség után (pont) | 0,5 |
| `kizart_tipusok` | ezeket nem vizsgálja (pl. Babakötvény, KTJ) | BABA, KTJ I, KTJ II, nyomdai MÁP Plusz |
| `deviza.cel_eur_arany`, `sav_pp` | cél EUR-arány és sáv | 15%, ±5 pont |
| `deviza.valtas_koltseg_pct` | banki váltási költség (feltevés) | 0,5% |
| `inflacio.HUF` / `EUR` | inflációs feltevések évenként (a folyó évre üres = KSH év eleje óta) | 2027: 3,0 / 2,0 |
| `dkj_3h`, `euribor_3h` | változó kamatok feltevése (üres = a mostani szint) | – |
| `visszavaltas.alap` | kincstári vételi nettó árfolyam | 99% |
| `tartalek`, `min_vasarlas` | befektetetlen tartalék és legkisebb vétel, devizánként | 100 000 Ft / 0 EUR |

Az inflációs feltevést negyedévente érdemes frissíteni, az MNB inflációs jelentése alapján.

## Adatforrások

Minden forrás nyilvános. A program naponta egyszer tölt le, a gyorsítótár helye: `data/allampapir/adatok_ÉÉÉÉ-HH-NN.json`.

| forrás | mit ad |
|---|---|
| **allampapir.hu (ÁKK)** belső JSON-API (CSRF-tokennel): `/api/paper/all_with_meta`, `/api/retailInterest/get_all_data`, `/api/networkRate/get_prices`, `/api/paper/details` + `/api/paper/get_files` | termékek, az összes lakossági sorozat kamatperiódusai, a most kapható sorozatok, kincstári árfolyamok, nyilvános ajánlattételek (PDF → prémium, sávos kamat) |
| **KSH STADAT** `ara0040` | fogyasztói árindex, havi és év eleje óta számolt átlag |
| **Eurostat** `prc_hicp_aind` | euróövezeti éves átlagos HICP |
| **ECB Data Portal** | 3 havi EURIBOR, EUR/HUF referenciaárfolyam |

Az allampapir.hu adatszolgáltatása **nem hivatalos, nyilvános felület**. Ha az oldal megváltozik, a `scripts/allampapir_adatok.py` javításra szorulhat. Letöltési hiba esetén a program a legutóbbi letöltött adattal fut, és a riport figyelmeztet erre.

Részletek: [`docs/01_allampapir_kinalat_es_adatforrasok.md`](docs/01_allampapir_kinalat_es_adatforrasok.md), [`docs/02_negyedeves_ajanlo.md`](docs/02_negyedeves_ajanlo.md).

## Könyvtárszerkezet

```
app/            webapp.py (helyi szerver, csak Python standard könyvtár + a projekt modulok), index.html
scripts/        allampapir_adatok.py   – adatletöltés (ÁKK, KSH, Eurostat, ECB)
                mak_portfolio_import.py – WebKincstár portfolio.xls → holdings.csv / cash.csv
                negyedeves_ajanlo.py   – kamatvetítés, csere, devizaarány, riport
                config_kezelo.py       – a config ellenőrzése és mentése
                pages_build.py         – a GitHub Pages oldal (napi kínálat + minta-riport)
config/         strategia.yaml (szabályok és feltevések)
docs/           részletes leírás, forrásdokumentum
minta/          portfolio_minta.xls (kitalált adatok)
site/           a Pages-oldal buildje – NEM kerül gitbe
.github/        a napi Pages-build munkafolyamata
private/        a saját portfólió, riportok, exportok – NEM kerül gitbe (.gitignore)
data/           letöltött adatok gyorsítótára – NEM kerül gitbe
```

## GitHub Pages (napi kínálat)

A `.github/workflows/pages.yml` naponta 05:15 UTC-kor, minden pushnál és kézi indításra lefuttatja a `scripts/pages_build.py` szkriptet:

1. friss adatokat tölt le;
2. a **minta-portfólióra** lefuttatja az ajánlót;
3. statikus oldalt készít (`site/`), és kiteszi a Pages-re.

Az oldalon az áll, ami mindenkinek érdekes: a most kapható papírok, az újdonságok, a feltevések és a teljes minta-riport. Személyes portfólió soha nem kerül oda.

Helyben is kipróbálható: `python scripts/pages_build.py`, majd nyisd meg a `site/index.html` fájlt.

Saját forkban a bekapcsolás: *Settings → Pages → Build and deployment → Source: GitHub Actions*.

## Adatvédelem és biztonság

- A portfólióadatok csak helyben, a `private/` mappában vannak. Az ügyfél neve és a számlaszám importkor nem kerül mentésre. A `private/` mappa a `.gitignore`-ban van.
- A webfelület csak a `127.0.0.1` címen figyel. Minden módosító kéréshez az indításkor generált token kell, és a szerver a Host-fejlécet is ellenőrzi. Az oldal semmit nem tölt be az internetről.

## Ismert korlátok

- Egyszerűsített hozamszámítás: időarányos átlagkamat, kamatos kamat és munkaszüneti napok nélkül. Összehasonlításra jó, pontos EHM-re nem.
- A jövőbeli lépések a mai kínálattal számolnak. Negyedévente újra kell futtatni.
- Egy új terméktípust a program a mostani kamatával számol, amíg a kamatképletét be nem építik (`ISMERT_TIPUSOK`, `build()`).
- A WebKincstár XLS-formátumának változása az importer javítását igényelheti.

## Licenc

[MIT](LICENSE). A program „ahogy van” alapon, mindennemű garancia nélkül használható. Nem befektetési tanácsadás: a döntésekért és a kötésekért a felhasználó felel.
