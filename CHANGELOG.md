# Változások

A verziószám egyetlen forrása a `VERSION` fájl. Kiírja a webfelület fejléce, a riport alja, a GitHub Pages oldal és a parancssori `--version` kapcsoló.
A kiadásokat git-tag jelöli (`v1.0`, `v1.1`, …).

## 1.1 – 2026-10-10

- **GitHub Pages:** napi kínálati oldal (https://kirlaca.github.io/MAP/).
  - Tartalma: a most kapható papírok vetített kamata, az újdonságok, a feltevések és a teljes riport a minta-portfólióra.
  - Működése: `scripts/pages_build.py` és a `.github/workflows/pages.yml` munkafolyamat. Naponta 05:15 UTC-kor, pushra és kézzel fut.
- **Minta-portfólió figyelmeztetés:** ha a betöltött állomány a minta (`minta/portfolio_minta.xls`), figyelmeztető doboz jelenik meg, a saját WebKincstár-export betöltésének lépéseivel. Helyei:
  - a webfelület Áttekintés és Ajánló fülén, és a Feltöltés előnézetében már import előtt;
  - a riport elején;
  - a Pages-oldalon.

  A felismerés a kitalált ügyfél és számlaszám alapján történik. Az állapot a `private/portfolio_meta.json` fájlban van.
- **Verziózás:** `VERSION` fájl, `scripts/verzio.py`, `--version` kapcsoló minden szkriptben, és a verzió kiírása a felületen, a riportban és a Pages-oldalon.
- **MIT licenc.**

## 1.0 – 2026-10-10 (első publikus változat)

- WebKincstár `portfolio.xls` import: az ügyfél neve és a számlaszám nem kerül mentésre.
- Adatletöltés, napi gyorsítótárral:
  - allampapir.hu (ÁKK): kínálat, kamattörténet, kincstári árfolyamok, ajánlattételek (PDF → prémium, sávos kamat);
  - KSH: infláció;
  - Eurostat: HICP;
  - ECB: EURIBOR, EUR/HUF.
- Kamatvetítés típusonként (PMÁP, PEMÁP, BMÁP, EMÁP, Fix MÁP, MÁP Plusz).
- Csereelemzés: a csere legjobb időpontja (most vagy egy kamatforduló után), csereküszöb a visszaváltási költség után.
- Cél EUR-arány sávval, a szabad pénz és a lejáratok irányításával, lejárat előtti eladás nélkül. Fedezeti EUR/HUF árfolyam.
- „Várható nominális többlet / év” oszlop a javasolt lépéseknél, zöld kiemeléssel.
- Kínálatfigyelés: az új és a megszűnt termékek, sorozatok jelzése. Az új sorozatok automatikusan bekerülnek a vizsgálatba.
- Helyi webfelület:
  - fülek: Áttekintés, Feltöltés, Ajánló, Beállítások, Útmutató;
  - a Beállítások fülön űrlap és nyers YAML, ellenőrzéssel és archiválással;
  - az Útmutatóban technikai rész az adatforrásokról.
- Kitalált minta-portfólió, README.
