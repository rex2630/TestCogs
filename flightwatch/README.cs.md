# FlightWatch

[English](README.md) | [Čeština](README.cs.md)

FlightWatch vyhledává a sleduje nejnižší jednosměrný tarif Ryanairu pro trasu a kalendářní měsíc. Do Discordu může poslat upozornění při změně ceny, novém minimu nebo dosažení volitelného cenového limitu. Příkazy fungují jako slash i jako prefix příkazy.

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
| `/flight search <odlet> <cíl> <rok> <měsíc> [měna]` | Najde nejnižší tarif Ryanairu pro měsíc. |
| `/flight watch <odlet> <cíl> <rok> <měsíc> [měna] [cenový_limit]` | Sleduje měsíční minimum a volitelně hlídá cenový limit. |
| `/flight watches` | Vypíše sledované trasy na serveru. |
| `/flight unwatch <id>` | Odebere sledování podle jeho ID. |
| `/flight check` | Okamžitě zkontroluje sledované ceny. |
| `/flight language <auto|cs|en>` | Nastaví jazyk odpovědí na serveru. |

Používej třípísmenné IATA kódy letišť. Příklady:

```text
/flight search PRG STN 2026 11 EUR
/flight watch PRG STN 2026 11 EUR 40
```

Sledování se kontrolují každých 30 minut. Upozornění obsahuje odkaz na rezervaci u Ryanairu. Bot letenky nekupuje.

## Rozsah a omezení

Tato verze sleduje pouze Ryanair. Pro danou trasu a měsíc vyhledává nejlevnější vrácený tarif; nesleduje každý jednotlivý let, zpáteční itineráře ani jiné aerolinky. Skyscanner není součástí, protože jeho Live Prices API vyžaduje partnerský přístup a veřejně garantovaný bezplatný tarif nenabízí.

Balíček `ryanair-py` používá neoficiální a nedokumentovaná rozhraní Ryanairu. Mohou se změnit, omezovat požadavky nebo vracet cenu odlišnou od konečné částky při rezervaci. Kontrola po 30 minutách není realtime feed. Před nákupem cenu vždy ověř na webu Ryanairu.

## Ukládaná data

Cog ukládá sledované trasy, měnu, volitelný cenový limit, poslední a nejnižší cenu, ID kanálů pro upozornění a jazyk serveru v Red Configu. Neukládá osobní profilová data.
