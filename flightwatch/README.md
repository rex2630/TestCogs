# FlightWatch

[English](README.md) | [Čeština](README.cs.md)

FlightWatch searches and tracks Ryanair's lowest one-way fare for a route and a calendar month. It can post an alert to Discord when the fare changes, reaches a new low, or crosses an optional price limit. Commands work as both prefix and slash commands.

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
| `/flight search <origin> <destination> <year> <month> [currency]` | Find the lowest Ryanair fare for a month. |
| `/flight watch <origin> <destination> <year> <month> [currency] [max_price]` | Track the monthly low and optionally alert at a price limit. |
| `/flight watches` | List this server's tracked routes. |
| `/flight unwatch <id>` | Remove a watch by its ID. |
| `/flight check` | Check tracked prices immediately. |
| `/flight language <auto|cs|en>` | Set the response language for this server. |

Use three-letter IATA airport codes. Examples:

```text
/flight search PRG STN 2026 11 EUR
/flight watch PRG STN 2026 11 EUR 40
```

Watches are checked every 30 minutes. Alerts include a Ryanair booking link. The bot does not purchase tickets.

## Scope and limitations

This version tracks Ryanair only. It finds the cheapest fare returned for a route and month; it does not monitor every individual flight, return itineraries, or other airlines. Skyscanner is not included because its Live Prices API requires partner access and does not offer a publicly guaranteed free tier.

`ryanair-py` uses Ryanair's unofficial, undocumented endpoints. They may change, rate-limit requests, or return a price that differs from the final booking price. A 30-minute polling interval is not a real-time feed. Always verify the fare on Ryanair before booking.

## Data storage

The cog stores tracked routes, selected currency, optional price limit, recent and lowest prices, alert channel IDs, and the server language in Red's Config. It does not store personal profile data.
