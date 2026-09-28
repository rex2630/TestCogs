# FlightWatch

[English](README.md) | [Čeština](README.cs.md)

FlightWatch searches Ryanair fares without creating a watch, compares low fares to multiple destinations, and tracks one-way or return fare lows. Return searches and watches compare complete outbound/inbound combinations in the date windows you provide. It posts alerts when a tracked fare changes, reaches a new low, or crosses an optional price limit. Replies, help, validation, and alerts are translated using separate Czech and English locale files. Commands work as both prefix and slash commands.

## Installation

Add the repository and install the cog:

```text
[p]repo add TestCogs https://github.com/rex2630/TestCogs
[p]cog install TestCogs flightwatch
[p]load flightwatch
```

The cog installs `ryanair-py` as a dependency. No API key is required.

## Language

By default, the cog follows the server's preferred locale: Czech servers use Czech, and other locales use English. Set a language explicitly with:

```text
/flight language en
/flight language cs
/flight language auto
```

`auto` follows the server locale again.

## Commands

| Command | Description |
| --- | --- |
| `/flight explore <origin> <from> <to> [currency]` | Show the five cheapest destinations from an airport. Search only; nothing is saved. |
| `/flight search <origin> <destination> <year> <month> [currency]` | Find the lowest Ryanair fare for a month. |
| `/flight returnsearch <origin> <destination> <outbound_from> <outbound_to> <return_from> <return_to> [currency]` | Find the lowest combined return fare in the supplied date windows. Dates use `YYYY-MM-DD`. |
| `/flight watch <origin> <destination> <year> <month> [currency] [max_price]` | Track the monthly low and optionally alert at a price limit. |
| `/flight returnwatch <origin> <destination> <outbound_year> <outbound_month> <return_year> <return_month> [currency] [max_price]` | Track the lowest total return fare across the selected calendar months. |
| `/flight returnwatchdates <origin> <destination> <outbound_from> <outbound_to> <return_from> <return_to> [currency] [max_price]` | Advanced: track exact outbound and return date windows (`YYYY-MM-DD`). |
| `/flight watches` | List this server's tracked routes. |
| `/flight unwatch <id>` | Remove a watch by its ID. |
| `/flight check` | Check tracked prices immediately. |
| `/flight airports` | Get the official IATA airport-code lookup. |
| `/flight language <auto|cs|en>` | Set the response language for this server. |

Use three-letter IATA airport codes. Examples:

```text
/flight explore DUB 2026-10-01 2026-10-07 EUR
/flight search PRG STN 2026 11 EUR
/flight returnsearch PRG STN 2026-11-01 2026-11-10 2026-11-05 2026-11-20 EUR
/flight watch PRG STN 2026 11 EUR 40
/flight returnwatch PRG STN 2026 11 2026 12 EUR 80
/flight returnwatchdates PRG STN 2026-11-01 2026-11-10 2026-11-05 2026-11-20 EUR 80
```

`search`, `returnsearch`, and `explore` are non-persistent searches. Only `watch` and `returnwatch` save a tracker. Return commands evaluate complete outbound-and-inbound combinations, rather than adding unrelated one-way fares; the supplied windows control the possible travel dates and stay length. Prices are for one traveller because this API wrapper does not expose reliable passenger-count pricing. Watches are checked every 30 minutes. Alerts include a Ryanair booking link. The bot does not purchase tickets. Use `/flight airports` for IATA codes or [IATA's official code search](https://www.iata.org/en/publications/directories/code-search/).

## Scope and limitations

This version tracks Ryanair only. Monthly one-way watches track the lowest fare for a selected route/month; return watches track the lowest total for explicit outbound and return date windows. It does not monitor individual fare buckets or other airlines. Skyscanner is not included because its Live Prices API requires partner access and does not offer a publicly guaranteed free tier.

`ryanair-py` uses Ryanair's unofficial, undocumented endpoints. They may change, rate-limit requests, or return a price that differs from the final booking price. A 30-minute polling interval is not a real-time feed. Always verify the fare on Ryanair before booking.

## Data storage

The cog stores tracked routes/date windows, selected currency, optional price limit, recent and lowest prices, alert channel IDs, and the server language in Red's Config. It does not store personal profile data.
