import io
import asyncio
import os
import pandas as pd
import urllib.request
import json
import ssl
from datetime import datetime, timezone
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
import fastf1
import fastf1.plotting
from matplotlib import pyplot as plt
from dotenv import load_dotenv

load_dotenv()

fastf1.Cache.disabled()

DIVIDER = "─" * 20 + "\n"


def pos_label(pos: int) -> str:
    return {1: "🥇", 2: "🥈", 3: "🥉"}.get(pos, f"*{pos}.*")


def format_lap_time(td):
    if pd.isna(td) or td is None:
        return "—"
    try:
        total_seconds = td.total_seconds()
    except AttributeError:
        try:
            total_seconds = float(td) / 1e9
        except Exception:
            return str(td)
    minutes = int(total_seconds // 60)
    seconds = total_seconds % 60
    if minutes > 0:
        return f"{minutes}:{seconds:06.3f}"
    else:
        return f"{seconds:.3f}"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "🏎️ *F1 ANALYTICS BOT*\n"
        "_Real-Time F1 Stats, Standings & Telemetry_\n"
        f"{DIVIDER}"
        "*📊 Telemetry & Results*\n"
        "`/speed` _<year> <location> <d1> <d2>_\n"
        "`/compare` _<year> <location> <d1> <d2>_\n"
        "`/results` _<year> <location>_\n"
        "`/quali` _<year> <location>_\n"
        "`/last_race` _most recent GP results_\n"
        f"{DIVIDER}"
        "*📅 Schedules & Standings*\n"
        "`/next_race` _upcoming GP countdown_\n"
        "`/schedule` _[year] — full calendar_\n"
        "`/driver_standings` _[year]_\n"
        "`/team_standings` _[year]_\n"
        f"{DIVIDER}"
        "*👤 Info*\n"
        "`/driver` _<code> — e.g. /driver HAM_\n"
        "`/track` _<year> <location>_\n"
        "`/help` _detailed docs_\n"
        f"{DIVIDER}"
        "💡 _First run of a session may take 15–30s while FastF1 caches data._"
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "📖 *F1 BOT — HELP*\n"
        f"{DIVIDER}"
        "*🏎️ /speed* — Fastest lap speed plot\n"
        "_Usage:_ `/speed 2023 Spa VER HAM`\n\n"
        "*⚔️ /compare* — Head-to-head race stats\n"
        "_Usage:_ `/compare 2023 Monza LEC SAI`\n\n"
        "*⏱️ /quali* — Q1 / Q2 / Q3 results\n"
        "_Usage:_ `/quali 2023 Monaco`\n\n"
        "*📍 /track* — Circuit details & coordinates\n"
        "_Usage:_ `/track 2024 Silverstone`\n\n"
        "*📅 /schedule* — Season calendar\n"
        "_Usage:_ `/schedule 2024`\n\n"
        "*👤 /driver* — Driver profile\n"
        "_Usage:_ `/driver VER`\n"
        f"{DIVIDER}"
        "*⚙️ Caching*\n"
        "First query per session downloads up to 50 MB. "
        "Subsequent queries are instant from local cache."
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")


async def speed_compare(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 4:
        await update.message.reply_text(
            "⚠️ *Usage:* `/speed <year> <location> <d1> <d2>`\n"
            "_Example:_ `/speed 2023 Monza VER HAM`",
            parse_mode="Markdown"
        )
        return

    try:
        year = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Year must be a number, e.g. `2023`", parse_mode="Markdown")
        return

    driver1 = context.args[-2].upper()
    driver2 = context.args[-1].upper()
    location = " ".join(context.args[1:-2])

    await update.message.reply_text(
        f"⏳ *Loading telemetry…*\n"
        f"_{year} {location} — {driver1} vs {driver2}_\n"
        f"_First run may take 15–30s_",
        parse_mode="Markdown"
    )

    try:
        session = fastf1.get_session(year, location, "R")
        session.load(telemetry=False, weather=False, messages=False)

        laps_d1 = session.laps.pick_drivers(driver1).pick_fastest()
        laps_d2 = session.laps.pick_drivers(driver2).pick_fastest()

        tel_d1 = laps_d1.get_car_data().add_distance()
        tel_d2 = laps_d2.get_car_data().add_distance()

        fastf1.plotting.setup_mpl()
        fig, ax = plt.subplots(figsize=(10, 5))

        color1 = fastf1.plotting.get_driver_color(driver1, session=session)
        color2 = fastf1.plotting.get_driver_color(driver2, session=session)
        linestyle2 = "--" if color1 == color2 else "-"

        ax.plot(tel_d1["Distance"], tel_d1["Speed"], color=color1, label=driver1)
        ax.plot(tel_d2["Distance"], tel_d2["Speed"], color=color2, linestyle=linestyle2, label=driver2)
        ax.set_xlabel("Distance (m)")
        ax.set_ylabel("Speed (km/h)")
        ax.legend()
        plt.title(f"{year} {location} — Fastest Lap: {driver1} vs {driver2}")

        buf = io.BytesIO()
        plt.savefig(buf, format="png", bbox_inches="tight")
        buf.seek(0)
        plt.close(fig)

        await update.message.reply_photo(photo=buf)

    except Exception as e:
        await update.message.reply_text(
            "❌ *Could not generate plot.*\n"
            "_Check year, location, and driver codes (e.g. VER, HAM)._",
            parse_mode="Markdown"
        )
        print(f"Internal error: {str(e)}")


async def get_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    year = datetime.now().year
    if len(context.args) > 0:
        try:
            year = int(context.args[0])
        except ValueError:
            await update.message.reply_text(
                "⚠️ *Usage:* `/schedule [year]`\n_Example:_ `/schedule 2024`",
                parse_mode="Markdown"
            )
            return

    await update.message.reply_text(f"⏳ *Fetching {year} calendar…*", parse_mode="Markdown")
    try:
        schedule = fastf1.get_event_schedule(year)
        schedule = schedule[schedule["RoundNumber"] > 0]

        if schedule.empty:
            await update.message.reply_text(f"❌ No schedule found for {year}.", parse_mode="Markdown")
            return

        lines = [f"📅 *F1 CALENDAR {year}*\n{DIVIDER}"]
        for _, row in schedule.iterrows():
            rd = int(row.get("RoundNumber", 0))
            name = row.get("EventName", "Unknown GP").replace(" Grand Prix", " GP")
            date_val = row.get("EventDate")
            date_str = date_val.strftime("%b %d") if date_val and not pd.isna(date_val) else "TBC"
            lines.append(f"*Rd {rd}* · {name} · _{date_str}_")

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    except Exception as e:
        await update.message.reply_text(f"❌ Could not retrieve schedule: `{str(e)}`", parse_mode="Markdown")


async def race_results(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text(
            "⚠️ *Usage:* `/results <year> <location>`\n_Example:_ `/results 2023 Monza`",
            parse_mode="Markdown"
        )
        return

    try:
        year = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Year must be a number, e.g. `2023`", parse_mode="Markdown")
        return

    location = " ".join(context.args[1:])
    await update.message.reply_text(f"⏳ *Fetching {year} {location} results…*", parse_mode="Markdown")

    try:
        session = fastf1.get_session(year, location, "R")
        session.load(telemetry=False, weather=False, messages=False)

        results = session.results
        if results.empty:
            await update.message.reply_text(f"❌ No results found for {year} {location}.", parse_mode="Markdown")
            return

        results = results.sort_values(by="Position")
        event_name = session.event.get("EventName", "Grand Prix")

        lines = [f"🏆 *{event_name} {year}*\n_{location}_\n{DIVIDER}"]
        for _, row in results.head(10).iterrows():
            pos = int(row.get("Position", 0))
            driver = row.get("Abbreviation", "UNK")
            team = row.get("TeamName", "Unknown")
            points = row.get("Points", 0.0)
            pts_str = f"{int(points)}" if points == int(points) else f"{points}"
            lines.append(f"{pos_label(pos)} *{driver}* — {team} — `{pts_str} pts`")

        lines.append(f"\n💡 _Try_ `/compare {year} {location} <d1> <d2>`")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    except Exception as e:
        await update.message.reply_text(
            "❌ Could not fetch results. Check spelling and try again.",
            parse_mode="Markdown"
        )
        print(f"Internal error: {str(e)}")


async def get_driver_standings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    year = datetime.now().year
    if len(context.args) > 0:
        try:
            year = int(context.args[0])
        except ValueError:
            await update.message.reply_text(
                "⚠️ *Usage:* `/driver_standings [year]`\n_Example:_ `/driver_standings 2024`",
                parse_mode="Markdown"
            )
            return

    await update.message.reply_text(f"⏳ *Fetching {year} driver standings…*", parse_mode="Markdown")
    try:
        url = f"https://api.jolpi.ca/ergast/f1/{year}/driverStandings.json"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        context_ssl = ssl._create_unverified_context()
        with urllib.request.urlopen(req, context=context_ssl) as response:
            data = json.loads(response.read().decode())

        standings_lists = data.get("MRData", {}).get("StandingsTable", {}).get("StandingsLists", [])
        if not standings_lists:
            await update.message.reply_text(f"❌ No driver standings for {year}.", parse_mode="Markdown")
            return

        driver_standings_data = standings_lists[0].get("DriverStandings", [])
        if not driver_standings_data:
            await update.message.reply_text(f"❌ No driver standings for {year}.", parse_mode="Markdown")
            return

        round_num = standings_lists[0].get("round", "N/A")

        lines = [f"🏆 *DRIVER STANDINGS {year}*\n_After Round {round_num}_\n{DIVIDER}"]
        for entry in driver_standings_data[:15]:
            pos = int(entry.get("position", 0))
            points = entry.get("points", "0")
            wins = entry.get("wins", "0")
            driver = entry.get("Driver", {})
            code = driver.get("code", driver.get("familyName", "UNK")[:3].upper())
            constructors = entry.get("Constructors", [])
            team = constructors[0].get("name", "Unknown") if constructors else "Unknown"
            lines.append(f"{pos_label(pos)} *{code}* — {team}\n   `{points} pts` · {wins} wins")

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    except Exception as e:
        await update.message.reply_text(f"❌ Could not fetch driver standings: `{str(e)}`", parse_mode="Markdown")
        print(f"Internal error: {str(e)}")


async def get_team_standings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    year = datetime.now().year
    if len(context.args) > 0:
        try:
            year = int(context.args[0])
        except ValueError:
            await update.message.reply_text(
                "⚠️ *Usage:* `/team_standings [year]`\n_Example:_ `/team_standings 2024`",
                parse_mode="Markdown"
            )
            return

    await update.message.reply_text(f"⏳ *Fetching {year} constructor standings…*", parse_mode="Markdown")
    try:
        url = f"https://api.jolpi.ca/ergast/f1/{year}/constructorStandings.json"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        context_ssl = ssl._create_unverified_context()
        with urllib.request.urlopen(req, context=context_ssl) as response:
            data = json.loads(response.read().decode())

        standings_lists = data.get("MRData", {}).get("StandingsTable", {}).get("StandingsLists", [])
        if not standings_lists:
            await update.message.reply_text(f"❌ No team standings for {year}.", parse_mode="Markdown")
            return

        constructor_standings_data = standings_lists[0].get("ConstructorStandings", [])
        if not constructor_standings_data:
            await update.message.reply_text(f"❌ No team standings for {year}.", parse_mode="Markdown")
            return

        round_num = standings_lists[0].get("round", "N/A")

        lines = [f"🏆 *CONSTRUCTOR STANDINGS {year}*\n_After Round {round_num}_\n{DIVIDER}"]
        for entry in constructor_standings_data:
            pos = int(entry.get("position", 0))
            points = entry.get("points", "0")
            wins = entry.get("wins", "0")
            name = entry.get("Constructor", {}).get("name", "Unknown")
            lines.append(f"{pos_label(pos)} *{name}*\n   `{points} pts` · {wins} wins")

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    except Exception as e:
        await update.message.reply_text(f"❌ Could not fetch constructor standings: `{str(e)}`", parse_mode="Markdown")
        print(f"Internal error: {str(e)}")


async def last_race_results(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ *Fetching last race results…*", parse_mode="Markdown")
    try:
        url = "https://api.jolpi.ca/ergast/f1/current/last/results.json"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        context_ssl = ssl._create_unverified_context()
        with urllib.request.urlopen(req, context=context_ssl) as response:
            data = json.loads(response.read().decode())

        race_data_list = data.get("MRData", {}).get("RaceTable", {}).get("Races", [])
        if not race_data_list:
            await update.message.reply_text("❌ No recent race results found.", parse_mode="Markdown")
            return

        race = race_data_list[0]
        season = race.get("season", "")
        round_num = race.get("round", "")
        race_name = race.get("raceName", "")
        circuit = race.get("Circuit", {}).get("circuitName", "")
        loc = race.get("Circuit", {}).get("Location", {})
        locality = loc.get("locality", "")
        country = loc.get("country", "")
        date = race.get("date", "")
        results = race.get("Results", [])

        lines = [
            f"🏁 *{race_name} {season}*",
            f"_Round {round_num} · {date}_",
            f"📍 {circuit}, {locality}, {country}",
            f"{DIVIDER}"
        ]
        for row in results[:10]:
            pos = int(row.get("position", 0))
            driver_code = row.get("Driver", {}).get("code", "UNK")
            team = row.get("Constructor", {}).get("name", "Unknown")
            points = row.get("points", "0")
            try:
                pts_f = float(points)
                pts_str = f"{int(pts_f)}" if pts_f == int(pts_f) else f"{pts_f}"
            except ValueError:
                pts_str = points
            lines.append(f"{pos_label(pos)} *{driver_code}* — {team} — `{pts_str} pts`")

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    except Exception as e:
        await update.message.reply_text(f"❌ Could not retrieve last race results: `{str(e)}`", parse_mode="Markdown")
        print(f"Internal error: {str(e)}")


async def driver_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 1:
        await update.message.reply_text(
            "⚠️ *Usage:* `/driver <code>`\n_Example:_ `/driver HAM`",
            parse_mode="Markdown"
        )
        return

    code = context.args[0].upper()
    await update.message.reply_text(f"⏳ *Looking up {code}…*", parse_mode="Markdown")

    try:
        url = "https://api.jolpi.ca/ergast/f1/current/drivers.json"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        context_ssl = ssl._create_unverified_context()
        with urllib.request.urlopen(req, context=context_ssl) as response:
            data = json.loads(response.read().decode())

        drivers_list = data.get("MRData", {}).get("DriverTable", {}).get("Drivers", [])
        matched_driver = next((d for d in drivers_list if d.get("code", "").upper() == code), None)

        if not matched_driver:
            await update.message.reply_text(
                f"❌ Driver code `{code}` not found in current season.\n"
                "_Try: VER, HAM, LEC, SAI, NOR…_",
                parse_mode="Markdown"
            )
            return

        name = f"{matched_driver.get('givenName', '')} {matched_driver.get('familyName', '')}"
        num = matched_driver.get("permanentNumber", "N/A")
        nat = matched_driver.get("nationality", "Unknown")
        dob = matched_driver.get("dateOfBirth", "Unknown")
        wiki = matched_driver.get("url", "")

        driver_card = (
            f"👤 *{name}*\n"
            f"{DIVIDER}"
            f"🔢 *Number:* {num}\n"
            f"🌍 *Nationality:* {nat}\n"
            f"🎂 *Date of Birth:* {dob}\n"
            f"🏷️ *Code:* `{code}`\n"
            f"{DIVIDER}"
            f"🔗 [Wikipedia Profile]({wiki})"
        )
        await update.message.reply_text(driver_card, parse_mode="Markdown", disable_web_page_preview=True)

    except Exception as e:
        await update.message.reply_text(f"❌ Could not retrieve driver profile: `{str(e)}`", parse_mode="Markdown")


async def qualifying_results(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text(
            "⚠️ *Usage:* `/quali <year> <location>`\n_Example:_ `/quali 2023 Monza`",
            parse_mode="Markdown"
        )
        return

    try:
        year = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Year must be a number, e.g. `2023`", parse_mode="Markdown")
        return

    location = " ".join(context.args[1:])
    await update.message.reply_text(f"⏳ *Fetching {year} {location} qualifying…*", parse_mode="Markdown")

    try:
        session = fastf1.get_session(year, location, "Q")
        session.load(telemetry=False, weather=False, messages=False)

        results = session.results
        if results.empty:
            await update.message.reply_text(f"❌ No qualifying results for {year} {location}.", parse_mode="Markdown")
            return

        results = results.sort_values(by="Position")
        event_name = session.event.get("EventName", "Grand Prix")

        p1_row = results.iloc[0]
        pole_driver = p1_row.get("FullName", p1_row.get("Abbreviation", "Unknown"))
        pole_time = format_lap_time(p1_row.get("Q3") or p1_row.get("Q2") or p1_row.get("Q1"))

        lines = [
            f"⏱️ *QUALIFYING — {event_name} {year}*",
            f"🥇 *Pole:* {pole_driver} — `{pole_time}`",
            f"{DIVIDER}"
        ]
        for _, row in results.head(10).iterrows():
            pos = int(row.get("Position", 0))
            driver = row.get("Abbreviation", "UNK")
            team = row.get("TeamName", "Unknown")
            q1 = format_lap_time(row.get("Q1"))
            q2 = format_lap_time(row.get("Q2"))
            q3 = format_lap_time(row.get("Q3"))
            best = q3 if q3 != "—" else (q2 if q2 != "—" else q1)
            lines.append(f"{pos_label(pos)} *{driver}* — {team}\n   `{best}`")

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    except Exception as e:
        await update.message.reply_text(
            "❌ Could not fetch qualifying results. Check year and location.",
            parse_mode="Markdown"
        )
        print(f"Internal error: {str(e)}")


async def track_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text(
            "⚠️ *Usage:* `/track <year> <location>`\n_Example:_ `/track 2023 Monza`",
            parse_mode="Markdown"
        )
        return

    try:
        year = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Year must be a number, e.g. `2023`", parse_mode="Markdown")
        return

    location = " ".join(context.args[1:])
    await update.message.reply_text(f"⏳ *Fetching {year} {location} track info…*", parse_mode="Markdown")

    try:
        url = f"https://api.jolpi.ca/ergast/f1/{year}.json"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        context_ssl = ssl._create_unverified_context()
        with urllib.request.urlopen(req, context=context_ssl) as response:
            data = json.loads(response.read().decode())

        races = data.get("MRData", {}).get("RaceTable", {}).get("Races", [])
        loc_clean = location.lower().strip()
        matched_race = None
        for r in races:
            circuit = r.get("Circuit", {})
            locality = circuit.get("Location", {}).get("locality", "").lower()
            country = circuit.get("Location", {}).get("country", "").lower()
            circuit_name = circuit.get("circuitName", "").lower()
            race_name = r.get("raceName", "").lower()
            if loc_clean in locality or loc_clean in country or loc_clean in circuit_name or loc_clean in race_name:
                matched_race = r
                break

        if not matched_race:
            await update.message.reply_text(
                f"❌ No track matching `{location}` found for {year}.",
                parse_mode="Markdown"
            )
            return

        race_name = matched_race.get("raceName", "Unknown GP")
        round_num = matched_race.get("round", "N/A")
        date_str = matched_race.get("date", "N/A")
        circuit = matched_race.get("Circuit", {})
        circuit_name = circuit.get("circuitName", "Unknown")
        wiki_url = circuit.get("url", "")
        loc_details = circuit.get("Location", {})
        locality = loc_details.get("locality", "Unknown")
        country = loc_details.get("country", "Unknown")
        lat = loc_details.get("lat", "N/A")
        lon = loc_details.get("long", "N/A")

        total_laps = "N/A"
        try:
            ff1_session = fastf1.get_session(year, round_num, "R")
            ff1_session.load(telemetry=False, weather=False, messages=False)
            res = ff1_session.results
            if not res.empty:
                total_laps = str(int(res["Laps"].max()))
        except Exception:
            pass

        track_card = (
            f"📍 *{race_name} {year}*\n"
            f"{DIVIDER}"
            f"🏟️ *Circuit:* {circuit_name}\n"
            f"📅 *Date:* {date_str} · Round {round_num}\n"
            f"🌍 *Location:* {locality}, {country}\n"
            f"🧭 *Coordinates:* {lat}°, {lon}°\n"
            f"🔄 *Total Laps:* {total_laps}\n"
            f"{DIVIDER}"
            f"🔗 [Circuit Guide]({wiki_url})"
        )
        await update.message.reply_text(track_card, parse_mode="Markdown", disable_web_page_preview=True)

    except Exception as e:
        await update.message.reply_text(f"❌ Could not retrieve track info: `{str(e)}`", parse_mode="Markdown")


async def next_race(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ *Finding next race…*", parse_mode="Markdown")
    try:
        year = datetime.now().year
        context_ssl = ssl._create_unverified_context()

        url = f"https://api.jolpi.ca/ergast/f1/{year}.json"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, context=context_ssl) as response:
                data = json.loads(response.read().decode())
        except Exception:
            year -= 1
            url = f"https://api.jolpi.ca/ergast/f1/{year}.json"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, context=context_ssl) as response:
                data = json.loads(response.read().decode())

        races = data.get("MRData", {}).get("RaceTable", {}).get("Races", [])
        if not races:
            await update.message.reply_text("❌ No race schedule found.", parse_mode="Markdown")
            return

        now_utc = datetime.now(timezone.utc)
        next_r = None

        for r in races:
            date_str = r.get("date", "")
            time_str = r.get("time", "00:00:00Z")
            if not time_str.endswith("Z"):
                time_str += "Z"
            try:
                race_dt = datetime.strptime(f"{date_str}T{time_str}", "%Y-%m-%dT%H:%M:%S%z")
            except ValueError:
                try:
                    race_dt = datetime.strptime(f"{date_str}T{time_str}", "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                except ValueError:
                    race_dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if race_dt > now_utc:
                next_r = (r, race_dt)
                break

        if not next_r:
            await update.message.reply_text(
                "🏁 *Season complete!*\n"
                "Use `/last_race` for final results or `/schedule` for the calendar.",
                parse_mode="Markdown"
            )
            return

        race, race_dt = next_r
        race_name = race.get("raceName", "Unknown GP")
        round_num = race.get("round", "N/A")
        circuit = race.get("Circuit", {}).get("circuitName", "Unknown")
        locality = race.get("Circuit", {}).get("Location", {}).get("locality", "Unknown")
        country = race.get("Circuit", {}).get("Location", {}).get("country", "Unknown")

        delta = race_dt - now_utc
        days = delta.days
        hours = delta.seconds // 3600
        minutes = (delta.seconds % 3600) // 60
        countdown_str = f"{days}d {hours}h {minutes}m"

        sessions_mapping = [
            ("FirstPractice", "🔧 FP1"),
            ("SecondPractice", "🔧 FP2"),
            ("ThirdPractice", "🔧 FP3"),
            ("Qualifying", "⏱️ Quali"),
            ("Sprint", "🏃 Sprint"),
        ]

        lines = [
            f"⏰ *{race_name} {year}*",
            f"_Round {round_num}_",
            f"📍 {circuit}, {locality}, {country}",
            f"{DIVIDER}"
            f"🚨 *Countdown:* `{countdown_str}`",
            f"{DIVIDER}"
            "*Weekend Schedule (UTC)*",
        ]

        for api_key, label in sessions_mapping:
            if api_key in race:
                s = race[api_key]
                lines.append(f"{label}: {s.get('date', '')} `{s.get('time', '')}`")

        race_time = race.get("time", "")
        lines.append(f"🏁 *Race:* {race.get('date', '')} `{race_time}`")
        lines.append(f"\n💡 _Use /quali and /speed after sessions finish!_")

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    except Exception as e:
        await update.message.reply_text(f"❌ Could not retrieve next race info: `{str(e)}`", parse_mode="Markdown")


async def driver_comparison(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 4:
        await update.message.reply_text(
            "⚠️ *Usage:* `/compare <year> <location> <d1> <d2>`\n"
            "_Example:_ `/compare 2023 Monza VER HAM`",
            parse_mode="Markdown"
        )
        return

    try:
        year = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Year must be a number, e.g. `2023`", parse_mode="Markdown")
        return

    driver1 = context.args[-2].upper()
    driver2 = context.args[-1].upper()
    location = " ".join(context.args[1:-2])

    await update.message.reply_text(
        f"⏳ *Fetching {year} {location}…*\n_{driver1} vs {driver2}_",
        parse_mode="Markdown"
    )

    try:
        session = fastf1.get_session(year, location, "R")
        session.load(telemetry=False, weather=False, messages=False)

        results = session.results
        if results.empty:
            await update.message.reply_text(f"❌ No results for {year} {location}.", parse_mode="Markdown")
            return

        row1 = results[results["Abbreviation"] == driver1]
        row2 = results[results["Abbreviation"] == driver2]

        if row1.empty:
            await update.message.reply_text(f"❌ `{driver1}` not found in {year} {location}.", parse_mode="Markdown")
            return
        if row2.empty:
            await update.message.reply_text(f"❌ `{driver2}` not found in {year} {location}.", parse_mode="Markdown")
            return

        row1 = row1.iloc[0]
        row2 = row2.iloc[0]

        event_name = session.event.get("EventName", "Grand Prix")

        def val(row, key, cast=str):
            try:
                return cast(row.get(key, "—"))
            except Exception:
                return "—"

        grid1, grid2 = val(row1, "GridPosition", int), val(row2, "GridPosition", int)
        pos1, pos2 = val(row1, "Position", int), val(row2, "Position", int)
        pts1 = f"{int(row1.get('Points', 0))}"
        pts2 = f"{int(row2.get('Points', 0))}"
        laps1, laps2 = val(row1, "Laps", int), val(row2, "Laps", int)
        fl1 = format_lap_time(row1.get("FastestLapTime"))
        fl2 = format_lap_time(row2.get("FastestLapTime"))
        status1 = str(row1.get("Status", "—"))
        status2 = str(row2.get("Status", "—"))

        lines = [
            f"⚔️ *{driver1} vs {driver2}*",
            f"🏁 {event_name} {year}",
            f"{DIVIDER}"
            f"🏎️ *Grid:* {driver1} P{grid1} → {driver2} P{grid2}\n"
            f"🏆 *Finish:* {driver1} P{pos1} → {driver2} P{pos2}\n"
            f"💰 *Points:* {driver1} `{pts1}` → {driver2} `{pts2}`\n"
            f"🔄 *Laps:* {driver1} {laps1} → {driver2} {laps2}\n"
            f"⚡ *Fastest Lap:*\n"
            f"  {driver1} `{fl1}`\n"
            f"  {driver2} `{fl2}`\n"
            f"✅ *Status:*\n"
            f"  {driver1} — {status1}\n"
            f"  {driver2} — {status2}",
            f"\n💡 _Try_ `/speed {year} {location} {driver1} {driver2}`"
        ]

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    except Exception as e:
        await update.message.reply_text(f"❌ Could not fetch comparison: `{str(e)}`", parse_mode="Markdown")
        print(f"Internal error: {str(e)}")


def main():
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

    if not TOKEN or TOKEN == "YOUR_TELEGRAM_BOT_TOKEN":
        print("ERROR: TELEGRAM_BOT_TOKEN is not set correctly in your environment or .env file.")
        return

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("speed", speed_compare))
    app.add_handler(CommandHandler("schedule", get_schedule))
    app.add_handler(CommandHandler("results", race_results))
    app.add_handler(CommandHandler("driver_standings", get_driver_standings))
    app.add_handler(CommandHandler("team_standings", get_team_standings))
    app.add_handler(CommandHandler("last_race", last_race_results))
    app.add_handler(CommandHandler("driver", driver_info))
    app.add_handler(CommandHandler("quali", qualifying_results))
    app.add_handler(CommandHandler("track", track_info))
    app.add_handler(CommandHandler("next_race", next_race))
    app.add_handler(CommandHandler("compare", driver_comparison))

    print("F1 Bot is running...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
