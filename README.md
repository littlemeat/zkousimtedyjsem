# Zkouším, tedy jsem

Osobní blog na [zkousimtedyjsem.cz](https://zkousimtedyjsem.cz) — Hugo + téma PaperMod, hostované na GitHub Pages.

## Jak napsat nový článek

```bash
hugo new posts/nazev-clanku/index.md
```

Vznikne složka `content/posts/nazev-clanku/` se souborem `index.md` — do stejné složky patří i fotky k článku. Nový článek má automaticky `draft: true`, takže se nepublikuje, dokud ho nepřepneš.

V hlavičce článku vyplň:

- `categories` — jednu z **Pracuju / Testuju / Cestuju** (s velkým písmenem)
- `description` a `summary` — krátký popis (zobrazuje se ve výpisu a ve vyhledávačích)
- `cover` — úvodní fotka (viz komentář v šabloně)

## Fotky

- Fotky patří do složky článku, vkládají se přes `![popisek](fotka.jpg)`.
- **Před nahráním fotky zmenši** na max ~2000 px delší strany (Náhled na Macu: Nástroje → Upravit velikost). Fotka pak má ~300–500 kB místo několika MB a repozitář zbytečně neroste.

## Publikování

1. V článku přepni `draft: true` na `draft: false`.
2. Commit a push do větve `main` — GitHub Actions web automaticky sestaví a nasadí (trvá ~2 minuty).

## Lokální náhled

```bash
git submodule update --init   # jen poprvé (stáhne téma)
hugo server -D                # -D zobrazí i drafty
```

Web pak běží na http://localhost:1313.

## Affiliate odkazy

Používej střídmě — jen v článcích, kde o produktu opravdu píšeš. Takový článek musí mít na konci upozornění (řádek je připravený v šabloně článku) a odkaz na [affiliate prohlášení](https://zkousimtedyjsem.cz/affiliate-prohlaseni/).
