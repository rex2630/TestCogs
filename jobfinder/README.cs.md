# JobFinder

[English](README.md) | [Čeština](README.cs.md)

JobFinder sbírá nabídky z českých veřejných API, mezinárodních pracovních API a vlastních RSS/Atom feedů. Sjednocuje je do přehledných embedů, páruje s neveřejnými profily uživatelů a může samostatně publikovat veřejný news feed. Příkazy fungují jako slash i prefix příkazy Redu. Odpovědi a embedy respektují jazyk serveru; uživatel si může jazyk upozornění změnit.

## Instalace

```text
[p]repo add TestCogs https://github.com/rex2630/TestCogs
[p]cog install TestCogs jobfinder
[p]load jobfinder
```

## Zdroje a četnost kontrol

Ve výchozím stavu jsou zapnuté tyto zdroje:

| Zdroj | Integrace | Výchozí kontrola |
| --- | --- | --- |
| [Síť práce](https://sitprace.cz/pro-vyvojare) | Veřejné JSON API; české nabídky a denně aktualizovaná data MPSV na portálu, 100 nejnovějších | 60 min |
| [Upjobs](https://upjobs.cz/api/v1/jobs) | Veřejné stránkované JSON API; strukturovaná mzda, lokalita, režim, úvazek a seniorita | 60 min |
| [MPSV / Úřad práce ČR](https://data.mpsv.cz/web/data/prirustky-volnych-mist-za-celou-cr) | Oficiální denní přírůstky nabídek za poslední dny; nestahuje se celý 185MB soubor | 24 h |
| [Remotive](https://remotive.com/api/remote-jobs) | Veřejné API vzdálených nabídek | 6 h |
| [Arbeitnow](https://www.arbeitnow.com/api/job-board-api) | Veřejné API pracovního portálu | 60 min |

Kontrolní smyčka běží každých 5 minut a výsledky providerů sdílí mezi servery. Výchozí intervaly jednotlivých zdrojů jsou nezávislé. Příkaz `/jobset interval <minuty>` mění interval českých a vlastních feedů; Remotive zůstává omezené nejvýše na čtyři požadavky denně. První úspěšné načtení naplní databázi bez rozeslání starších nabídek. Nové nabídky se posílají při dalších kontrolách.

Jobs.cz a Prace.cz nemají obecné veřejné API bez přihlášení. Jejich oficiální integrace vyžadují partnerský přístup, proto cog jejich stránky nescrapuje. [API StartupJobs](https://firmy.startupjobs.cz/cs/articles/9506864-propojeni-s-vasim-ats) je určené pro nabídky konkrétní firmy a vyžaduje její bearer token; [partnerské API Careerjet](https://www.careerjet.cz/partners/api) potřebuje publisher klíč a IP adresu uživatele, který vyhledávání spustil. Nejde tedy o univerzální feed pro celý server. Vlastní RSS/Atom feed lze přidat, pokud ho portál nebo zaměstnavatel nabízí. Některé nabídky MPSV záměrně neobsahují zaměstnavatele ani přímý odkaz; embed v takovém případě jasně označí Úřad práce a odkáže na jeho datovou sadu.

## Preference uživatelů

Příkaz `/job preferences` otevře soukromý editor filtrů. Obsahuje modální formulář a dropdowny; profil se zobrazuje jen soukromě nebo v DM.

| Preference | Jak se páruje |
| --- | --- |
| Hledaná slova | V textu nabídky se musí objevit alespoň jedna fráze |
| Vyloučená slova | Jakákoliv shoda nabídku skryje |
| Města a regiony | Porovnávají se s polem lokality nabídky |
| Typ úvazku | Rozpoznává běžné ekvivalenty jako HPP/full-time, DPP, DPČ/part-time a IČO/contractor |
| Rozmezí mzdy | Volitelné minimum a maximum v Kč za měsíc; hodinové mzdy MPSV se pro filtrování přibližně převedou na měsíční |
| Režim práce | Libovolný, vzdálený, hybridní nebo na pracovišti; zvolený režim vyžaduje údaj od zdroje |
| Seniorita | Libovolná, junior, medior, senior nebo lead/management |
| Četnost upozornění | Poslat shodu po další kontrole zdrojů, nebo ji seskupit do denního souhrnu v 18:00 UTC |
| Jazyk upozornění | Podle jazyka serveru, česky nebo anglicky |

Před zapnutím upozornění je potřeba vyplnit alespoň klíčové slovo, lokalitu nebo typ úvazku. Filtry pracují s údaji poskytnutými zdrojem; neznámý režim nebo chybějící mzda nevyhoví přísnému filtru režimu či mzdy.

Prefixový příkaz pro základní profil:

```text
[p]job set "python, backend" "Praha, Brno" "HPP, DPP"
```

Další osobní příkazy:

```text
/job preferences
/job enable
/job disable
/job language auto
```

## Upozornění a embedy

Osobní upozornění chodí soukromě do DM. Embed zobrazuje zdroj nad i pod nabídkou a odkaz na poskytovatele, zaměstnavatele, lokalitu, mzdu, úvazek, režim práce, senioritu, obor, datum zveřejnění a při DM také skóre shody s profilem. Název nabídky vede na inzerát, pokud zdroj přímý odkaz poskytuje. I bez něj zůstává klikací odkaz na zdrojová data.

Veřejný news kanál zapneš takto:

```text
/jobset channel #job-news
/jobset news true
/jobset role @JobAlerts
```

Veřejné příspěvky jsou ve výchozím stavu vypnuté. Nové nabídky se seskupují do menších dávek embedů; nastavená role se označí jednou při publikování dávky. Příkaz `/jobset news false` vypne veřejný feed, osobní DM dál fungují.

## Příkazy serveru

| Příkaz | Popis |
| --- | --- |
| `/jobset sources` | Vypíše nastavené zdroje |
| `/jobset source <url> [name]` | Přidá RSS nebo Atom feed |
| `/jobset remove <index>` | Odebere zdroj podle pořadového čísla |
| `/jobset interval <minuty>` | Nastaví interval českých a vlastních zdrojů, 5 minut až 24 hodin |
| `/jobset run` | Zkontroluje zdroje, jejichž interval právě uplynul |
| `/jobset channel <channel>` | Nastaví veřejný news kanál |
| `/jobset news <true\|false>` | Zapne nebo vypne veřejné příspěvky |
| `/jobset role [role]` | Nastaví nebo odebere označovanou roli |

## Data a omezení

Osobní filtry a nastavení upozornění se ukládají v Red Configu, nastavení zdrojů pro každý server zvlášť. Normalizované nabídky a historie doručení se ukládají do místní SQLite databáze, aby se nabídka neposílala opakovaně. Osobní upozornění vypneš příkazem `/job disable`; Discord musí povolovat DM od členů serveru. Red při požadavku na smazání uživatelských dat odstraní profil i historii doručení.

Veřejná API mohou měnit pole nebo dostupnost. Některé zdroje ukazují pouze nedávné nabídky nebo neuvádějí mzdu, zaměstnavatele či lokalitu. Data MPSV se obnovují denně a někteří zaměstnavatelé žádají Úřad práce o skrytí identity a přímého odkazu. JobFinder odkazuje na zdroj a neodesílá žádosti o práci.
