# 01 – Lakossági állampapír-kínálat és adatforrások (2026-10-10)

## Most kapható (allampapir.hu, underIssue)
| papír | deviza | lejárat | kamat | EHM | megjegyzés |
|---|---|---|---|---|---|
| Fix MÁP 2029/Q2 | HUF | 2029-10-25 | 5,50% fix | 5,62% | negyedéves kamatfizetés, 3 év |
| MÁP Plusz 2031/M6 | HUF | 2031-11-27 | 5,00 → 6,00% sávos | 5,47% | a kamat papírban jóváíródik; évente 5 munkanapig 100%-on visszaváltható |
| BMÁP 2032/R1 | HUF | 2032-06-22 | 5,47% (3 havi DKJ 5,22% + 0,25%) | – | negyedévente fordul; a prémium 2030-06-tól 0,50% |
| PMÁP 2036/I1 | HUF | 2036-02-21 | 4,50% az első periódusban (2027-02-21-ig), utána a KSH-infláció + **0,10%** | – | |
| EMÁP 2029/U | EUR | 2029-10-25 | 2,493% (3 havi EURIBOR) | – | negyedévente fordul |
| Babakötvény, Kincstári Takarékjegy, nyomdai MÁP Plusz | | | | | az ajánló nem veszi figyelembe |

A Prémium Euró MÁP (PEMÁP) 2023-03-17 óta nem kapható, a forgalomban lévő sorozatok lejáratig futnak.

## Visszaváltás a Kincstárnál
- A kincstári árfolyamjegyzés szerint a lejárat előtti vételi árfolyam a PMÁP, BMÁP, Fix MÁP, PEMÁP és EMÁP sorozatokon **nettó 99,00% + felhalmozott kamat**.
- MÁP Plusz: nettó 99%. A kamatjuttatás utáni 5 munkanapban 100%, összeghatártól függetlenül.
- A WebKincstár XLS-exportjában szereplő árfolyam ez a bruttó vételi árfolyam (99 + felhalmozott kamat), így a "piaci érték" a mai visszaváltási érték.

## A kamat képlete típusonként
- **PMÁP:** a kamatmegállapítás évét megelőző év KSH éves átlagos fogyasztói árindexe (ha negatív, akkor 0) + prémium. Éves kamatfordulás.
  Példák régebbi sorozatok prémiumára: 2027/I 1,50%, 2028/I 1,50%, 2033/I 0,25%.
- **PEMÁP:** az előző év euróövezeti éves átlagos HICP-je + prémium (2028/X 0,50%, 2028/Y 0,25%), éves fordulással. A 2027/X régi, 3 éves sorozat: félévente fordul, havi inflációs bázissal.
- **BMÁP (2022-09-30 után kibocsátott):** a kamatmegállapítás előtti 4 sikeres 3 havi DKJ-aukció átlaghozama + prémium. Negyedéves fordulás.
- **EMÁP:** 3 havi EURIBOR (+ prémium, a 2029/U-nál 0). Negyedéves fordulás.
- **Fix MÁP / MÁP Plusz:** fix, illetve az ajánlattételben rögzített sávos kamat.

## Kulcstény 2026-10-ben
A KSH szerint 2026 januárja és szeptembere között az átlagos infláció **1,7%**, 2025-ben **4,4%** volt. Ezért a PMÁP-sorozatok 2027-es fordulója után a kamat erősen esik, például:
- 2033/I: 4,65% → kb. 1,95%;
- 2028/I: 5,90% → kb. 3,20%.

Közben a Fix MÁP fix 5,50%-ot fizet.

## Adatforrások (a `scripts/allampapir_adatok.py` tölti le, naponta egyszer)
- **allampapir.hu belső JSON API.** SvelteKit oldal; előbb CSRF-token kell: `GET /api/csrf`, utána POST `X-CSRF-TOKEN` fejléccel.
  - `/api/paper/all_with_meta {"separate":1}`: a termékek.
  - `/api/retailInterest/get_all_data {}`: az összes lakossági sorozat összes kamatperiódusa 2000-től; `underIssue` = most kapható.
  - `/api/networkRate/get_prices {"paper": "MAP"|"MAPP"|"DKJ"|"EMAP"|"BABA"}`: a Kincstár napi vételi és eladási árfolyamai. A "MAP" fülön vannak a PMÁP, BMÁP és Fix sorozatok is.
  - `/api/paper/details {"slug"}` → `paper_id` → `/api/paper/get_files {"paper": paper_id}`: a nyilvános ajánlattételek (PDF). Ezekből olvassuk ki a kamatprémiumot és a sávos kamatokat (pypdf).
- **KSH STADAT ara0040** (HTML): havi és év eleje óta kumulált fogyasztói árindex. Böngésző User-Agent és Accept-Language fejléc kell, különben a KSH tűzfala elutasítja. A CSV-változatot mindig elutasítja.
- **Eurostat `prc_hicp_aind`**: az euróövezeti éves átlagos HICP. A havi `prc_hicp_manr` 2025-12-nél megáll (módszertanváltás), és az ECB régi ICP-adatsora is megszűnt 2026-02-ben. Ezért a folyó évi EUR-infláció a configból jön.
- **ECB Data Portal**: 3 havi EURIBOR havi átlag (`FM/M.U2.EUR.RT.MM.EURIBOR3MD_.HSTA`) és EUR/HUF (`EXR/D.HUF.EUR.SP00.A`).
