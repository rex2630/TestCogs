# FlightWatch

[English](README.md) | [Čeština](README.cs.md)

FlightWatch umí vyhledat tarif Ryanairu bez založení sledování, porovnat nejlevnější lety do více destinací a sledovat jednosměrná i zpáteční cenová minima. Zpáteční hledání a sledování porovnává celé kombinace odletu a návratu v zadaných časových oknech. Do Discordu posílá upozornění při změně ceny, novém minimu nebo dosažení volitelného cenového limitu. Odpovědi, nápověda, validace i upozornění jsou v oddělených českých a anglických locale souborech. Příkazy fungují jako slash i jako prefix příkazy.

## Instalace

Přidej repozitář a nainstaluj cog:

```text
[p]repo add TestCogs https://github.com/rex2630/TestCogs
[p]cog install TestCogs flightwatch
[p]load flightwatch
```

Cog si nainstaluje závislost `ryanair-py`. API klíč není potřeba.

## Jazyk

Ve výchozím stavu cog respektuje jazyk nastavený na serveru: české servery dostanou češtinu, ostatní angličtinu. Jazyk lze změnit:

```text
/flight language cs
/flight language en
/flight language auto
```

Volba `auto` znovu použije jazyk serveru.

## Příkazy

| Příkaz | Popis |
| --- | --- |
| `/flight explore <odlet> <od_data> <do_data> [měna]` | Ukáže pět nejlevnějších destinací z letiště. Jen vyhledává, nic neukládá. |
| `/flight search <odlet> <cíl> <rok> <měsíc> [měna]` | Najde nejnižší tarif Ryanairu pro měsíc. |
| `/flight returnsearch <odlet> <cíl> <odlet_od> <odlet_do> <návrat_od> <návrat_do> [měna]` | Najde nejlevnější zpáteční kombinaci v zadaných termínech. Data zadej jako `RRRR-MM-DD`. |
| `/flight watch <odlet> <cíl> <rok> <měsíc> [měna] [cenový_limit]` | Sleduje měsíční minimum a volitelně hlídá cenový limit. |
| `/flight returnwatch <odlet> <cíl> <rok_odletu> <měsíc_odletu> <rok_návratu> <měsíc_návratu> [měna] [limit_celkem]` | Sleduje nejnižší celkovou cenu zpáteční cesty v celých měsících. |
| `/flight returnwatchdates <odlet> <cíl> <odlet_od> <odlet_do> <návrat_od> <návrat_do> [měna] [limit_celkem]` | Pokročilé: sleduje přesná okna ve formátu `RRRR-MM-DD`. |
| `/flight watches` | Vypíše sledované trasy na serveru. |
| `/flight unwatch <id>` | Odebere sledování podle jeho ID. |
| `/flight check` | Okamžitě zkontroluje sledované ceny. |
| `/flight airports` | Otevře oficiální vyhledávání letištních IATA kódů. |
| `/flight language <auto|cs|en>` | Nastaví jazyk odpovědí na serveru. |

Používej třípísmenné IATA kódy letišť. Příklady:

```text
/flight explore DUB 2026-10-01 2026-10-07 EUR
/flight search PRG STN 2026 11 EUR
/flight returnsearch PRG STN 2026-11-01 2026-11-10 2026-11-05 2026-11-20 EUR
/flight watch PRG STN 2026 11 EUR 40
/flight returnwatch PRG STN 2026 11 2026 12 EUR 80
/flight returnwatchdates PRG STN 2026-11-01 2026-11-10 2026-11-05 2026-11-20 EUR 80
```

`search`, `returnsearch` a `explore` jsou nezávazná hledání a nic neukládají. Sledování založí až `watch` nebo `returnwatch`. Zpáteční příkazy porovnávají kompletní kombinace odletu a návratu, nesčítají nesouvisející jednosměrné letenky. Zadaná okna určují možné termíny cesty i délku pobytu. Ceny jsou pro jednoho cestujícího; wrapper neumí spolehlivě ocenit více osob. Sledování se kontrolují každých 30 minut. Upozornění obsahují odkaz na rezervaci, bot letenky nekupuje. Pro kódy použij `/flight airports` nebo [oficiální vyhledávání IATA](https://www.iata.org/en/publications/directories/code-search/).

## Rozsah a omezení

Tato verze sleduje pouze Ryanair. Jednosměrné sledování hlídá minimum pro trasu a měsíc; zpáteční sledování hlídá celkovou cenu pro zadaná odletová a návratová okna. Cog nesleduje jednotlivé cenové úrovně sedadel ani jiné aerolinky. Skyscanner není součástí, protože jeho Live Prices API vyžaduje partnerský přístup a veřejně garantovaný bezplatný tarif nenabízí.

Balíček `ryanair-py` používá neoficiální a nedokumentovaná rozhraní Ryanairu. Mohou se změnit, omezovat požadavky nebo vracet cenu odlišnou od konečné částky při rezervaci. Kontrola po 30 minutách není realtime feed. Před nákupem cenu vždy ověř na webu Ryanairu.

## Ukládaná data

Cog ukládá sledované trasy a termínová okna, měnu, volitelný cenový limit, poslední a nejnižší cenu, ID kanálů pro upozornění a jazyk serveru v Red Configu. Neukládá osobní profilová data.
