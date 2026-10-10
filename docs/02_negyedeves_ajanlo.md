# 02 – Negyedéves állampapír-ajánló (v1, 2026-10-10)

## Negyedéves futtatás
A legegyszerűbb a webfelület: `start_webapp.bat` → http://127.0.0.1:8766. Ott a Feltöltés, majd az Ajánló fül kell. Parancssorból:

1. WebKincstár → Portfólió → Excel export (`portfolio.xls`).
   `python scripts/mak_portfolio_import.py <portfolio.xls>`: felülírja a `private/holdings.csv` és a `private/cash.csv` fájlt, a régieket a `private/archiv/` mappába menti, és pillanatképet készít a `private/snapshots/` mappába. Előbb érdemes `--proba` kapcsolóval futtatni.
2. `python scripts/negyedeves_ajanlo.py`. A `--frissit` kapcsolóval a mai adat-cache-t is újratölti.
3. A riport helye: `private/reports/ajanlo_ÉÉÉÉ-QN.md`. A kötést a WebKincstárban kell beadni.

Negyedévente frissítsd a `config/strategia.yaml` `inflacio` részét (MNB inflációs jelentés: a jövő évi átlaginfláció).

## Mit csinál
- **Tartott sorozatok kamatpályája:**
  - A múltbeli és a folyó periódus kamata az ÁKK lakossági kamattáblájából jön.
  - A jövőbeli periódusokat a program az utolsó periódushossz ismétlésével, típusonként vetíti (lásd `docs/01`).
  - A prémiumot így számolja: ismert kamat − az alapjául szolgáló infláció. Új sorozatnál a nyilvános ajánlattételből olvassa ki.
  - Ha egyik sem megy, a `premium_kezi` beállítás adja meg.
- **Kínálat:** minden most kapható sorozat, kivéve a `kizart_tipusok` listát (Babakötvény, KTJ, nyomdai MÁP Plusz). A program mindegyikre kiszámolja a várható átlagkamatot a horizonton (`horizont_ev`, 3 év, vagy a lejáratig), és ez alapján rangsorol. A deviza a sorozatnévből jön (`_EUR` utótag).
- **Új termékek, új sorozatok:**
  - Az új sorozat ugyanabban az ÁKK-adatban jelenik meg, és automatikusan bekerül a vizsgálatba. Az ajánlattételét (prémium, sávos kamat) a program minden aktív terméknél letölti, a `NEM_KELL_AJANLATTETEL` tiltólistán lévők kivételével.
  - Egy **új terméktípust** is vizsgál, de annak a kamatképletét nem ismeri, ezért a mostani kamattal vagy EHM-mel számol a lejáratig. Ezt a kínálati tábla és a javasolt lépés is ⚠️-tel jelzi. A pontos vetítéshez a típust fel kell venni az `ISMERT_TIPUSOK` közé, és a `build()` függvénybe kell a képlete.
  - A nyilvántartás helye: `data/allampapir/ismert.json` (termékek és kapható sorozatok, első és utolsó észlelés). Az első futás csak felveszi a kiinduló állapotot (`kiindulo: true`).
  - Utána a riport *Újdonságok a kínálatban* része 92 napig mutatja az új és a megszűnt termékeket, illetve sorozatokat. A kizárt típusok sorozatait nem listázza.
- **Csereelemzés** minden tartott papírra, azonos devizában:
  - Nyereség = ∫ alternatíva − ∫ tartott − visszaváltási költség (1%). Az időablak: ma → min(lejárat, horizont).
  - Lehetséges eladási nap: ma, vagy a tartott papír egy későbbi kamatfordulója. Ha a folyó periódus kamata jobb, a program vár.
  - Akkor javasol cserét, ha a nyereség évesítve legalább `csere.min_elony_pp` (0,5 pont).
- **Szabad készpénz** (a tartalék felett, legalább `min_vasarlas`): a legjobb kapható papírba megy, alapból azonos devizában.
- **Devizaarány** (`deviza`: cél 15% EUR, ±5 pont sáv):
  - Ha az EUR-arány a sávon kívül van, a program időrendben végigmegy a szabad pénz és a lejárat lépésein, és a célig szükséges részt a másik devizába irányítja: *VÁLTÁS EUR → HUF + VÉTEL*.
  - A váltás bankszámlán történik, 0,5% költség feltevéssel. Tartott papírt emiatt nem ad el.
  - A riport *Devizaarány* része mutatja a mostani arányt, a tervet és a fedezeti pontot: azt az EUR/HUF árfolyamot, amely felett az euró jobban járt volna.
- **Lejáratok a következő 12 hónapban:** újrabefektetési javaslat a mai legjobb papírral. A következő futás ezt újraszámolja.
- **Pénzáramlás-naptár** (12 hónap, becsült kamatokkal), **lejárati létra** és a **feltevések** listája.


## Ismert korlátok
- Egyszerűsített hozamszámítás: időarányos átlagkamat, kamatos kamat és munkaszüneti napok nélkül. Összehasonlításra jó, pontos EHM-re nem.
- A jövőbeli cserék a mai kínálattal számolnak. A Fix MÁP kamata vagy elérhetősége 2027 februárjáig változhat. Ezért kell negyedévente újrafuttatni.
- BMÁP: ha nincs `dkj_3h` feltevés, a mostani kamat marad. Ha az MNB kamatot csökkent, a BMÁP-kamat is esik.
- PEMÁP 2027/X (félévente fordul, havi bázis): a mostani kamattal számol.
- A MÁP Plusz sávos kamata csak a most kapható sorozatnál ismert (az ajánlattételből). Egy tartott MÁP Plusz az EHM-mel számol.
- Az allampapir.hu API nem hivatalos. Ha az oldal megváltozik, a `scripts/allampapir_adatok.py` javításra szorulhat. A WebKincstár XLS-formátumára ugyanez igaz a `scripts/mak_portfolio_import.py`-nál.
