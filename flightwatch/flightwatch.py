import asyncio
from calendar import monthrange
import logging
import re
import uuid
from datetime import date
from typing import Optional
from urllib.parse import urlencode

from discord.ext import tasks
from redbot.core import Config, commands
from ryanair import Ryanair as RyanairAPI


log = logging.getLogger("red.flightwatch")
AIRPORT_RE = re.compile(r"^[A-Z]{3}$")
CHECK_INTERVAL_MINUTES = 30
MESSAGES = {
    "en": {
        "search_failed": "Search failed: {error}",
        "search_result": "Lowest fare: **{price:.2f} {currency}** ({origin} → {destination}, {date}).\n{url}",
        "return_search_result": "Cheapest return trip: **{total:.2f} {currency} total**\nOutbound: {origin} → {destination}, {out_date} — {out_price:.2f} {currency}\nReturn: {destination} → {origin}, {return_date} — {return_price:.2f} {currency}\n{url}",
        "return_search_failed": "Return search failed: {error}",
        "invalid_return_dates": "Enter valid ISO dates (YYYY-MM-DD) and make sure the return window does not start before the outbound window.",
        "no_return_fares": "Ryanair returned no matching return trips for those date windows.",
        "invalid_limit": "The price limit must be greater than zero.",
        "watch_failed": "Could not start fare tracking: {error}",
        "duplicate": "This route and month are already being tracked in this channel.",
        "watch_created": "Tracking **{origin} → {destination}**, {period}: current low **{price:.2f} {currency}** ({date}){cap}. Watch ID: `{watch_id}`. Checks every {minutes} minutes.",
        "watches_empty": "There are no fare alerts configured for this server.",
        "watch_line": "`{id}` {origin} → {destination} {period}: latest {price:.2f} {currency} (low {lowest:.2f})",
        "watch_unknown": "I couldn't find a watch with that ID.",
        "watch_removed": "Watch `{watch_id}` was removed.",
        "checking": "Checking tracked fares…",
        "check_done": "Fare check complete.",
        "invalid_airport": "Use three-letter IATA airport codes, for example PRG and STN.",
        "same_airport": "Origin and destination must be different airports.",
        "invalid_date": "The year or month is outside the supported range.",
        "past_month": "You cannot search a month that has already started in the past.",
        "invalid_currency": "Currency must be a three-letter code, for example EUR or GBP.",
        "no_fares": "Ryanair returned no fares for this route and month.",
        "minimum": "NEW LOWEST FARE",
        "day_changed": "Lowest-fare date changed",
        "price_down": "Fare dropped",
        "price_changed": "Fare changed",
        "limit_crossed": "\nPrice limit reached!",
        "language_set": "FlightWatch language set to **{language}**.",
        "language_invalid": "Choose `auto`, `cs`, or `en`.",
    },
    "cs": {
        "search_failed": "Vyhledávání se nepodařilo: {error}",
        "search_result": "Nejnižší cena: **{price:.2f} {currency}** ({origin} → {destination}, {date}).\n{url}",
        "return_search_result": "Nejlevnější zpáteční cesta: **celkem {total:.2f} {currency}**\nOdlet: {origin} → {destination}, {out_date} — {out_price:.2f} {currency}\nNávrat: {destination} → {origin}, {return_date} — {return_price:.2f} {currency}\n{url}",
        "return_search_failed": "Vyhledávání zpáteční cesty se nepodařilo: {error}",
        "invalid_return_dates": "Zadej platná data ve formátu RRRR-MM-DD a začátek návratu nesmí být před začátkem odletového okna.",
        "no_return_fares": "Ryanair pro zadaná časová okna nenašel odpovídající zpáteční lety.",
        "invalid_limit": "Cenový limit musí být vyšší než nula.",
        "watch_failed": "Sledování ceny se nepodařilo založit: {error}",
        "duplicate": "Tuto trasu a měsíc už v tomto kanálu sleduješ.",
        "watch_created": "Sleduji **{origin} → {destination}**, {period}: aktuální minimum **{price:.2f} {currency}** ({date}){cap}. ID sledování: `{watch_id}`. Kontrola každých {minutes} minut.",
        "watches_empty": "Na tomto serveru zatím není nastavené žádné sledování cen.",
        "watch_line": "`{id}` {origin} → {destination} {period}: naposledy {price:.2f} {currency} (minimum {lowest:.2f})",
        "watch_unknown": "Sledování s tímto ID jsem nenašel.",
        "watch_removed": "Sledování `{watch_id}` bylo odebráno.",
        "checking": "Kontroluji sledované ceny…",
        "check_done": "Kontrola cen dokončena.",
        "invalid_airport": "Použij třípísmenné IATA kódy letišť, například PRG a STN.",
        "same_airport": "Odletové a cílové letiště musí být rozdílné.",
        "invalid_date": "Rok nebo měsíc je mimo podporovaný rozsah.",
        "past_month": "Nelze hledat měsíc, který už začal v minulosti.",
        "invalid_currency": "Měna musí být třípísmenný kód, například EUR nebo GBP.",
        "no_fares": "Ryanair pro tuto trasu a měsíc nevrátil žádné tarify.",
        "minimum": "NOVÉ CENOVÉ MINIMUM",
        "day_changed": "Změnil se den nejnižší ceny",
        "price_down": "Cena klesla",
        "price_changed": "Cena se změnila",
        "limit_crossed": "\nBylo dosaženo cenového limitu!",
        "language_set": "Jazyk FlightWatch byl nastaven na **{language}**.",
        "language_invalid": "Vyber `auto`, `cs` nebo `en`.",
    },
}


class FlightWatch(commands.Cog):
    """Search Ryanair one-way and return fares and track monthly lows."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=7461829031, force_registration=True)
        self.config.register_guild(watches=[], language="auto")
        self._apis = {}
        self._api_lock = asyncio.Lock()

    async def cog_load(self):
        self.check_prices.start()

    async def cog_unload(self):
        self.check_prices.cancel()

    @commands.hybrid_group(name="flight", invoke_without_command=True)
    @commands.guild_only()
    async def flight(self, ctx):
        """Search Ryanair fares and manage fare alerts."""
        await ctx.send_help()

    @flight.command(name="search")
    async def search(
        self,
        ctx,
        origin: str,
        destination: str,
        year: int,
        month: int,
        currency: str = "EUR",
    ):
        """Find the cheapest Ryanair fare in a month (airport IATA codes)."""
        language = await self._get_language(ctx.guild)
        try:
            self._validate_search(origin, destination, year, month, currency, language)
            fares = await self._fetch_fares(origin.upper(), destination.upper(), year, month, currency.upper())
        except Exception as exc:
            error = MESSAGES[language]["no_fares"] if str(exc) == "NO_FARES" else str(exc)
            await ctx.send(MESSAGES[language]["search_failed"].format(error=error))
            return

        fare = fares[0]
        booking_url = self._booking_url(origin.upper(), destination.upper(), fare["date"])
        await ctx.send(MESSAGES[language]["search_result"].format(
            price=fare["price"], currency=currency.upper(), origin=origin.upper(),
            destination=destination.upper(), date=fare["date"], url=booking_url
        ))

    @flight.command(name="returnsearch")
    async def return_search(
        self,
        ctx,
        origin: str,
        destination: str,
        outbound_from: str,
        outbound_to: str,
        return_from: str,
        return_to: str,
        currency: str = "EUR",
    ):
        """Find the cheapest combined return fare within four ISO date bounds."""
        language = await self._get_language(ctx.guild)
        try:
            origin = origin.upper()
            destination = destination.upper()
            currency = currency.upper()
            self._validate_search(origin, destination, date.today().year, date.today().month, currency, language)
            try:
                outbound_start = date.fromisoformat(outbound_from)
                outbound_end = date.fromisoformat(outbound_to)
                return_start = date.fromisoformat(return_from)
                return_end = date.fromisoformat(return_to)
            except ValueError as exc:
                raise ValueError(MESSAGES[language]["invalid_return_dates"]) from exc
            if (
                outbound_start > outbound_end
                or return_start > return_end
                or outbound_start < date.today()
                or return_start < outbound_start
                or return_end < outbound_start
                or any(bound.year < 2024 or bound.year > 2035 for bound in (
                    outbound_start, outbound_end, return_start, return_end
                ))
            ):
                raise ValueError(MESSAGES[language]["invalid_return_dates"])
            trips = await self._fetch_return_fares(
                origin, destination, outbound_start, outbound_end,
                return_start, return_end, currency
            )
        except Exception as exc:
            error = MESSAGES[language]["no_return_fares"] if str(exc) == "NO_RETURN_FARES" else str(exc)
            await ctx.send(MESSAGES[language]["return_search_failed"].format(error=error))
            return

        trip = min(trips, key=lambda candidate: candidate.totalPrice)
        booking_url = self._booking_url(
            origin, destination, trip.outbound.departureTime.date().isoformat(),
            trip.inbound.departureTime.date().isoformat(),
        )
        await ctx.send(MESSAGES[language]["return_search_result"].format(
            total=trip.totalPrice, currency=trip.outbound.currency,
            origin=origin, destination=destination,
            out_date=trip.outbound.departureTime.date().isoformat(),
            out_price=trip.outbound.price,
            return_date=trip.inbound.departureTime.date().isoformat(),
            return_price=trip.inbound.price, url=booking_url,
        ))

    @flight.command(name="watch")
    async def watch(
        self,
        ctx,
        origin: str,
        destination: str,
        year: int,
        month: int,
        currency: str = "EUR",
        max_price: Optional[float] = None,
    ):
        """Watch the monthly lowest fare and alert on every price change."""
        language = await self._get_language(ctx.guild)
        try:
            origin = origin.upper()
            destination = destination.upper()
            currency = currency.upper()
            self._validate_search(origin, destination, year, month, currency, language)
            if max_price is not None and max_price <= 0:
                raise ValueError(MESSAGES[language]["invalid_limit"])
            fares = await self._fetch_fares(origin, destination, year, month, currency)
        except Exception as exc:
            error = MESSAGES[language]["no_fares"] if str(exc) == "NO_FARES" else str(exc)
            await ctx.send(MESSAGES[language]["watch_failed"].format(error=error))
            return

        watches = await self.config.guild(ctx.guild).watches()
        duplicate = any(
            item["origin"] == origin
            and item["destination"] == destination
            and item["year"] == year
            and item["month"] == month
            and item["currency"] == currency
            and item["channel_id"] == ctx.channel.id
            for item in watches
        )
        if duplicate:
            await ctx.send(MESSAGES[language]["duplicate"])
            return

        fare = fares[0]
        watch_id = uuid.uuid4().hex[:8]
        watches.append(
            {
                "id": watch_id,
                "origin": origin,
                "destination": destination,
                "year": year,
                "month": month,
                "currency": currency,
                "channel_id": ctx.channel.id,
                "owner_id": ctx.author.id,
                "max_price": max_price,
                "last_price": fare["price"],
                "lowest_price": fare["price"],
                "last_date": fare["date"],
            }
        )
        await self.config.guild(ctx.guild).watches.set(watches)
        cap = f", limit {max_price:.2f} {currency}" if max_price is not None else ""
        await ctx.send(MESSAGES[language]["watch_created"].format(
            origin=origin, destination=destination, period=f"{year}-{month:02d}",
            price=fare["price"], currency=currency, date=fare["date"], cap=cap,
            watch_id=watch_id, minutes=CHECK_INTERVAL_MINUTES
        ))

    @flight.command(name="watches")
    async def watches(self, ctx):
        """List fare alerts configured for this server."""
        language = await self._get_language(ctx.guild)
        entries = await self.config.guild(ctx.guild).watches()
        if not entries:
            await ctx.send(MESSAGES[language]["watches_empty"])
            return
        lines = [
            MESSAGES[language]["watch_line"].format(
                id=item["id"], origin=item["origin"], destination=item["destination"],
                period=f"{item['year']}-{item['month']:02d}", price=item["last_price"],
                currency=item["currency"], lowest=item["lowest_price"]
            )
            for item in entries
        ]
        await ctx.send("\n".join(lines))

    @flight.command(name="unwatch")
    async def unwatch(self, ctx, watch_id: str):
        """Remove a fare alert by its ID."""
        language = await self._get_language(ctx.guild)
        config = self.config.guild(ctx.guild).watches
        entries = await config()
        remaining = [item for item in entries if item["id"] != watch_id]
        if len(remaining) == len(entries):
            await ctx.send(MESSAGES[language]["watch_unknown"])
            return
        await config.set(remaining)
        await ctx.send(MESSAGES[language]["watch_removed"].format(watch_id=watch_id))

    @flight.command(name="check")
    async def check(self, ctx):
        """Check this server's tracked fares now."""
        language = await self._get_language(ctx.guild)
        await ctx.send(MESSAGES[language]["checking"])
        await self._check_guild(ctx.guild)
        await ctx.send(MESSAGES[language]["check_done"])

    @flight.command(name="language")
    async def set_language(self, ctx, language: str):
        """Set this server's language to auto, Czech (cs), or English (en)."""
        language = language.lower()
        current = await self._get_language(ctx.guild)
        if language not in {"auto", "cs", "en"}:
            await ctx.send(MESSAGES[current]["language_invalid"])
            return
        await self.config.guild(ctx.guild).language.set(language)
        selected = await self._get_language(ctx.guild)
        await ctx.send(MESSAGES[selected]["language_set"].format(language=selected))

    @staticmethod
    def _validate_search(origin, destination, year, month, currency, language):
        messages = MESSAGES[language]
        if not AIRPORT_RE.fullmatch(origin.upper()) or not AIRPORT_RE.fullmatch(destination.upper()):
            raise ValueError(messages["invalid_airport"])
        if origin.upper() == destination.upper():
            raise ValueError(messages["same_airport"])
        if not 1 <= month <= 12 or not 2024 <= year <= 2035:
            raise ValueError(messages["invalid_date"])
        if date(year, month, 1) < date.today().replace(day=1):
            raise ValueError(messages["past_month"])
        if not re.fullmatch(r"[A-Z]{3}", currency.upper()):
            raise ValueError(messages["invalid_currency"])

    async def _get_language(self, guild):
        language = await self.config.guild(guild).language()
        if language in {"cs", "en"}:
            return language
        locale = str(getattr(guild, "preferred_locale", "en-US")).lower()
        return "cs" if locale.startswith("cs") else "en"

    async def _get_api(self, currency):
        if currency not in self._apis:
            async with self._api_lock:
                if currency not in self._apis:
                    self._apis[currency] = await asyncio.to_thread(RyanairAPI, currency)
        return self._apis[currency]

    async def _fetch_fares(self, origin, destination, year, month, currency):
        api = await self._get_api(currency)
        date_from = date(year, month, 1)
        date_to = date(year, month, monthrange(year, month)[1])
        flights = await asyncio.to_thread(
            api.get_cheapest_flights,
            origin,
            date_from,
            date_to,
            destination_airport=destination,
        )
        fares = [
            {
                "date": flight.departureTime.date().isoformat(),
                "price": float(flight.price),
            }
            for flight in flights
            if flight.destination.upper() == destination
        ]
        fares.sort(key=lambda item: item["price"])
        if not fares:
            raise ValueError("NO_FARES")
        return fares

    async def _fetch_return_fares(
        self, origin, destination, outbound_start, outbound_end,
        return_start, return_end, currency,
    ):
        api = await self._get_api(currency)
        trips = await asyncio.to_thread(
            api.get_cheapest_return_flights,
            origin,
            outbound_start,
            outbound_end,
            return_start,
            return_end,
            destination_airport=destination,
        )
        if not trips:
            raise ValueError("NO_RETURN_FARES")
        return trips

    @staticmethod
    def _booking_url(origin, destination, departure, return_date=None):
        query = urlencode(
            {
                "adults": 1,
                "teens": 0,
                "children": 0,
                "infants": 0,
                "dateOut": departure,
                "dateIn": return_date or "",
                "isConnectedFlight": "false",
                "discount": 0,
                "promoCode": "",
                "originIata": origin,
                "destinationIata": destination,
            }
        )
        return f"https://www.ryanair.com/gb/en/trip/flights/select?{query}"

    @tasks.loop(minutes=CHECK_INTERVAL_MINUTES)
    async def check_prices(self):
        for guild in self.bot.guilds:
            await self._check_guild(guild)

    @check_prices.before_loop
    async def before_check_prices(self):
        await self.bot.wait_until_ready()

    async def _check_guild(self, guild):
        config = self.config.guild(guild).watches
        watches = await config()
        changed = False
        cache = {}
        language = await self._get_language(guild)
        for item in watches:
            key = (item["origin"], item["destination"], item["year"], item["month"], item["currency"])
            try:
                if key not in cache:
                    cache[key] = await self._fetch_fares(*key)
                    await asyncio.sleep(1)
                fare = cache[key][0]
                current_price = fare["price"]
                previous_price = item.get("last_price")
                new_minimum = current_price < item.get("lowest_price", current_price)
                crossed_limit = (
                    item.get("max_price") is not None
                    and current_price <= item["max_price"]
                    and (previous_price is None or previous_price > item["max_price"])
                )
                if previous_price is not None and (
                    current_price != previous_price or fare["date"] != item.get("last_date")
                ):
                    await self._notify(
                        guild, item, fare, previous_price, new_minimum, crossed_limit, language
                    )
                item["last_price"] = current_price
                item["lowest_price"] = min(item.get("lowest_price", current_price), current_price)
                item["last_date"] = fare["date"]
                changed = True
            except Exception as exc:
                log.warning("Fare check failed for %s -> %s: %s", item.get("origin"), item.get("destination"), exc)
        if changed:
            await config.set(watches)

    async def _notify(self, guild, item, fare, previous_price, new_minimum, crossed_limit, language):
        channel = guild.get_channel(item["channel_id"])
        if channel is None:
            return
        currency = item["currency"]
        current_price = fare["price"]
        if new_minimum:
            headline = MESSAGES[language]["minimum"]
        elif current_price == previous_price and fare["date"] != item.get("last_date"):
            headline = MESSAGES[language]["day_changed"]
        elif current_price < previous_price:
            headline = MESSAGES[language]["price_down"]
        else:
            headline = MESSAGES[language]["price_changed"]
        extra = MESSAGES[language]["limit_crossed"] if crossed_limit else ""
        booking_url = self._booking_url(item["origin"], item["destination"], fare["date"])
        try:
            await channel.send(
                f"**{headline}: {item['origin']} → {item['destination']}**\n"
                f"{previous_price:.2f} → **{current_price:.2f} {currency}** "
                f"({item.get('last_date')} → {fare['date']}).{extra}\n"
                f"{booking_url}"
            )
        except Exception:
            log.exception("Unable to send fare alert to channel %s", item["channel_id"])


async def setup(bot):
    await bot.add_cog(FlightWatch(bot))
