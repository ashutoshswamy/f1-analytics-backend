import os
import gc
import json
import asyncio
import urllib.request
import ssl
from datetime import datetime, timezone, timedelta
from functools import lru_cache
import pandas as pd
import numpy as np
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
import fastf1


app = FastAPI(title="F1 Analytics Bot API", description="High-performance backend API serving F1 telemetry and statistics")

# Enable CORS for Next.js frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

fastf1.Cache.disabled()

# Only 1 session load at a time — prevents concurrent RAM spikes on 512MB instance
_session_load_sem = asyncio.Semaphore(1)
_session_locks: dict = {}

@lru_cache(maxsize=4)
def _load_session_sync(year: int, location: str | int, session_type: str):
    s = fastf1.get_session(year, location, session_type)
    s.load(telemetry=False, weather=False, messages=False)
    return s

async def _load_session(year: int, location: str | int, session_type: str):
    key = (year, location, session_type)
    if key not in _session_locks:
        _session_locks[key] = asyncio.Lock()
    async with _session_locks[key]:
        async with _session_load_sem:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                None, _load_session_sync, year, location, session_type
            )


DRIVER_COLORS = {
    "VER": "#3671C6", "PER": "#3671C6",
    "HAM": "#27F4D2", "RUS": "#27F4D2",
    "LEC": "#E8002D", "SAI": "#E8002D",
    "NOR": "#FF8000", "PIA": "#FF8000",
    "ALO": "#358C75", "STR": "#358C75",
    "GAS": "#0093CC", "OCO": "#0093CC",
    "ALB": "#64C4FF", "SAR": "#64C4FF",
    "TSU": "#6692FF", "LAW": "#6692FF",
    "BOT": "#C92D4B", "ZHO": "#C92D4B",
    "MAG": "#B6BABD", "HUL": "#B6BABD",
    "BEA": "#B6BABD", "ANT": "#27F4D2",
    "DOO": "#FF8000", "HAD": "#6692FF",
}

def get_ssl_context():
    return ssl._create_unverified_context()

def fetch_openf1_json(url: str):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        context = get_ssl_context()
        with urllib.request.urlopen(req, context=context, timeout=30) as response:
            return json.loads(response.read().decode())
    except Exception as e:
        print(f"OpenF1 API Error ({url}): {e}")
        return None

async def fetch_openf1(url: str):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, fetch_openf1_json, url)

def format_lap_time_seconds(seconds):
    if seconds is None:
        return "-"
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        return "-"
    minutes = int(seconds // 60)
    secs = seconds % 60
    if minutes > 0:
        return f"{minutes}:{secs:06.3f}"
    return f"{secs:.3f}"

def fetch_ergast_json(url: str):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        context = get_ssl_context()
        with urllib.request.urlopen(req, context=context) as response:
            return json.loads(response.read().decode())
    except Exception as e:
        print(f"Ergast API Error ({url}): {e}")
        return None

def format_lap_time_td(td):
    if pd.isna(td) or td is None:
        return "-"
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
    return f"{seconds:.3f}"

@app.get("/api/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}

@app.get("/api/next_race")
async def get_next_race():
    try:
        year = datetime.now().year
        url = f"https://api.jolpi.ca/ergast/f1/{year}.json"
        data = fetch_ergast_json(url)
        if not data or not data.get("MRData", {}).get("RaceTable", {}).get("Races", []):
            year -= 1
            url = f"https://api.jolpi.ca/ergast/f1/{year}.json"
            data = fetch_ergast_json(url)

        races = data.get("MRData", {}).get("RaceTable", {}).get("Races", []) if data else []
        if not races:
            raise HTTPException(status_code=404, detail="No schedule found")

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
            return {"completed": True, "message": "Championship Season Completed!"}

        race, race_dt = next_r
        delta = race_dt - now_utc
        days = delta.days
        hours = delta.seconds // 3600
        minutes = (delta.seconds % 3600) // 60

        sessions = []
        sessions_mapping = [
            ("FirstPractice", "First Practice (FP1)"),
            ("SecondPractice", "Second Practice (FP2)"),
            ("ThirdPractice", "Third Practice (FP3)"),
            ("Qualifying", "Qualifying"),
            ("Sprint", "Sprint"),
            ("Race", "Grand Prix")
        ]
        for api_key, label in sessions_mapping:
            if api_key == "Race":
                sessions.append({"session": label, "date": race.get("date", ""), "time": race.get("time", "")})
            elif api_key in race:
                s_data = race.get(api_key, {})
                sessions.append({"session": label, "date": s_data.get("date", ""), "time": s_data.get("time", "")})

        return {
            "completed": False,
            "raceName": race.get("raceName", "Unknown GP"),
            "round": race.get("round", "N/A"),
            "circuitName": race.get("Circuit", {}).get("circuitName", "Unknown Circuit"),
            "locality": race.get("Circuit", {}).get("Location", {}).get("locality", "Unknown"),
            "country": race.get("Circuit", {}).get("Location", {}).get("country", "Unknown"),
            "countdown": {
                "days": days,
                "hours": hours,
                "minutes": minutes,
                "total_seconds": int(delta.total_seconds())
            },
            "sessions": sessions
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/last_race")
async def get_last_race():
    try:
        url = "https://api.jolpi.ca/ergast/f1/current/last/results.json"
        data = fetch_ergast_json(url)
        race_data_list = data.get("MRData", {}).get("RaceTable", {}).get("Races", []) if data else []
        if not race_data_list:
            raise HTTPException(status_code=404, detail="No recent race results found")

        race = race_data_list[0]
        results = race.get("Results", [])
        top_10 = []
        for row in results[:10]:
            driver = row.get("Driver", {})
            top_10.append({
                "position": int(row.get("position", 0)),
                "driver": driver.get("code", driver.get("familyName", "UNK")[:3].upper()),
                "driverFullName": f"{driver.get('givenName', '')} {driver.get('familyName', '')}",
                "team": row.get("Constructor", {}).get("name", "Unknown Team"),
                "points": float(row.get("points", 0.0)),
                "status": row.get("status", "Finished"),
                "grid": int(row.get("grid", 0))
            })

        return {
            "raceName": race.get("raceName", ""),
            "season": race.get("season", ""),
            "round": race.get("round", ""),
            "circuit": race.get("Circuit", {}).get("circuitName", ""),
            "locality": race.get("Circuit", {}).get("Location", {}).get("locality", ""),
            "country": race.get("Circuit", {}).get("Location", {}).get("country", ""),
            "date": race.get("date", ""),
            "results": top_10
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/schedule")
async def get_schedule(year: int = Query(default=None)):
    if year is None:
        year = datetime.now().year
    try:
        schedule = fastf1.get_event_schedule(year)
        schedule = schedule[schedule["RoundNumber"] > 0]
        if schedule.empty:
            raise HTTPException(status_code=404, detail="No schedule found")

        rounds = []
        for _, row in schedule.iterrows():
            date_val = row.get("EventDate")
            date_str = date_val.strftime("%Y-%m-%d") if date_val and not pd.isna(date_val) else ""
            rounds.append({
                "round": int(row.get("RoundNumber", 0)),
                "raceName": row.get("EventName", "Unknown GP").replace(" Grand Prix", ""),
                "location": row.get("Location", ""),
                "country": row.get("Country", ""),
                "date": date_str
            })
        return {"year": year, "schedule": rounds}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/results")
async def get_results(year: int, location: str):
    try:
        session = await _load_session(year, location, "R")
        results = session.results
        if results.empty:
            raise HTTPException(status_code=404, detail="No results found")

        results = results.sort_values(by="Position")
        top_10 = []
        for _, row in results.head(10).iterrows():
            top_10.append({
                "position": int(row.get("Position", 0)),
                "driver": row.get("Abbreviation", "UNK"),
                "driverFullName": row.get("FullName", "Unknown"),
                "team": row.get("TeamName", "Unknown"),
                "points": float(row.get("Points", 0.0)),
                "grid": int(row.get("GridPosition", 0)),
                "status": row.get("Status", "Finished"),
                "fastestLapTime": format_lap_time_td(row.get("FastestLapTime"))
            })
        return {
            "raceName": session.event.get('EventName', 'Grand Prix'),
            "year": year,
            "location": location,
            "results": top_10
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/quali")
async def get_quali(year: int, location: str):
    try:
        session = await _load_session(year, location, "Q")
        results = session.results
        if results.empty:
            raise HTTPException(status_code=404, detail="No qualifying results found")

        results = results.sort_values(by="Position")
        top_10 = []
        for _, row in results.head(10).iterrows():
            top_10.append({
                "position": int(row.get("Position", 0)),
                "driver": row.get("Abbreviation", "UNK"),
                "driverFullName": row.get("FullName", "Unknown"),
                "team": row.get("TeamName", "Unknown"),
                "q1": format_lap_time_td(row.get("Q1")),
                "q2": format_lap_time_td(row.get("Q2")),
                "q3": format_lap_time_td(row.get("Q3"))
            })

        pole_driver = "Unknown"
        pole_time = "N/A"
        if not results.empty:
            p1_row = results.iloc[0]
            pole_driver = p1_row.get("FullName", p1_row.get("Abbreviation", "Unknown"))
            pole_time = format_lap_time_td(p1_row.get("Q3") or p1_row.get("Q2") or p1_row.get("Q1"))

        return {
            "raceName": session.event.get('EventName', 'Grand Prix'),
            "year": year,
            "location": location,
            "poleDriver": pole_driver,
            "poleTime": pole_time,
            "results": top_10
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/standings/drivers")
async def get_driver_standings(year: int = Query(default=None)):
    if year is None:
        year = datetime.now().year
    try:
        url = f"https://api.jolpi.ca/ergast/f1/{year}/driverStandings.json"
        data = fetch_ergast_json(url)
        standings_lists = data.get("MRData", {}).get("StandingsTable", {}).get("StandingsLists", []) if data else []
        if not standings_lists:
            raise HTTPException(status_code=404, detail=f"No standings found for {year}")

        driver_standings = standings_lists[0].get("DriverStandings", [])
        result = []
        for ds in driver_standings:
            driver = ds.get("Driver", {})
            constructors = ds.get("Constructors", [])
            team = constructors[0].get("name", "Unknown") if constructors else "Unknown"
            result.append({
                "position": int(ds.get("position", 0)),
                "driver": driver.get("code", driver.get("familyName", "UNK")[:3].upper()),
                "driverFullName": f"{driver.get('givenName', '')} {driver.get('familyName', '')}",
                "team": team,
                "points": float(ds.get("points", 0.0)),
                "wins": int(ds.get("wins", 0))
            })
        return {"year": year, "round": standings_lists[0].get("round", "0"), "standings": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/standings/teams")
async def get_team_standings(year: int = Query(default=None)):
    if year is None:
        year = datetime.now().year
    try:
        url = f"https://api.jolpi.ca/ergast/f1/{year}/constructorStandings.json"
        data = fetch_ergast_json(url)
        standings_lists = data.get("MRData", {}).get("StandingsTable", {}).get("StandingsLists", []) if data else []
        if not standings_lists:
            raise HTTPException(status_code=404, detail=f"No standings found for {year}")

        constructor_standings = standings_lists[0].get("ConstructorStandings", [])
        result = []
        for cs in constructor_standings:
            constructor = cs.get("Constructor", {})
            result.append({
                "position": int(cs.get("position", 0)),
                "team": constructor.get("name", "Unknown"),
                "points": float(cs.get("points", 0.0)),
                "wins": int(cs.get("wins", 0))
            })
        return {"year": year, "round": standings_lists[0].get("round", "0"), "standings": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/driver")
async def get_driver_info(code: str):
    try:
        url = "https://api.jolpi.ca/ergast/f1/current/drivers.json"
        data = fetch_ergast_json(url)
        drivers = data.get("MRData", {}).get("DriverTable", {}).get("Drivers", []) if data else []
        matched = None
        for d in drivers:
            if d.get("code", "").upper() == code.upper():
                matched = d
                break
        if not matched:
            raise HTTPException(status_code=404, detail=f"Driver '{code}' not found")

        return {
            "code": code.upper(),
            "name": f"{matched.get('givenName', '')} {matched.get('familyName', '')}",
            "number": matched.get("permanentNumber", "N/A"),
            "nationality": matched.get("nationality", "Unknown"),
            "dob": matched.get("dateOfBirth", "Unknown"),
            "wiki": matched.get("url", "")
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/track")
async def get_track_info(year: int, location: str):
    try:
        url = f"https://api.jolpi.ca/ergast/f1/{year}.json"
        data = fetch_ergast_json(url)
        races = data.get("MRData", {}).get("RaceTable", {}).get("Races", []) if data else []
        matched_race = None
        loc_clean = location.lower().strip()
        for r in races:
            circuit = r.get("Circuit", {})
            locality = circuit.get("Location", {}).get("locality", "").lower()
            country = circuit.get("Location", {}).get("country", "").lower()
            circuit_name = circuit.get("circuitName", "").lower()
            race_name = r.get("raceName", "").lower()
            if (loc_clean in locality or loc_clean in country or loc_clean in circuit_name or loc_clean in race_name):
                matched_race = r
                break

        if not matched_race:
            raise HTTPException(status_code=404, detail=f"Track '{location}' not found for season {year}")

        circuit = matched_race.get("Circuit", {})
        loc_details = circuit.get("Location", {})

        total_laps = "N/A"
        try:
            ff1_session = await _load_session(year, int(matched_race.get("round", 1)), "R")
            results = ff1_session.results
            if not results.empty:
                total_laps = str(int(results["Laps"].max()))
        except Exception:
            pass

        return {
            "raceName": matched_race.get("raceName", "Unknown GP"),
            "round": matched_race.get("round", "N/A"),
            "circuitName": circuit.get("circuitName", "Unknown"),
            "locality": loc_details.get("locality", "Unknown"),
            "country": loc_details.get("country", "Unknown"),
            "latitude": loc_details.get("lat", "N/A"),
            "longitude": loc_details.get("long", "N/A"),
            "totalLaps": total_laps,
            "date": matched_race.get("date", "N/A"),
            "wiki": circuit.get("url", "")
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/telemetry")
async def get_telemetry(year: int, location: str, driver1: str, driver2: str):
    try:
        driver1 = driver1.upper()
        driver2 = driver2.upper()

        # 1. Find race session key
        sessions = await fetch_openf1(f"https://api.openf1.org/v1/sessions?year={year}&session_name=Race") or []
        loc_clean = location.lower().strip()
        session_key = None
        for s in sessions:
            country = (s.get("country_name") or "").lower()
            circuit = (s.get("circuit_short_name") or "").lower()
            loc_field = (s.get("location") or "").lower()
            if loc_clean in country or loc_clean in circuit or loc_clean in loc_field or country in loc_clean or circuit in loc_clean:
                session_key = s["session_key"]
                break

        if not session_key:
            raise HTTPException(status_code=404, detail=f"No race session found for {year} {location}")

        # 2. Fetch driver info in parallel
        d1_resp, d2_resp = await asyncio.gather(
            fetch_openf1(f"https://api.openf1.org/v1/drivers?session_key={session_key}&name_acronym={driver1}"),
            fetch_openf1(f"https://api.openf1.org/v1/drivers?session_key={session_key}&name_acronym={driver2}")
        )

        d1_resp = d1_resp or []
        d2_resp = d2_resp or []
        if not d1_resp:
            raise HTTPException(status_code=404, detail=f"Driver {driver1} not found in session")
        if not d2_resp:
            raise HTTPException(status_code=404, detail=f"Driver {driver2} not found in session")

        d1_num = d1_resp[0]["driver_number"]
        d2_num = d2_resp[0]["driver_number"]
        d1_fullname = d1_resp[0].get("full_name", driver1)
        d2_fullname = d2_resp[0].get("full_name", driver2)

        # 3. Fetch laps in parallel, find fastest
        laps1_raw, laps2_raw = await asyncio.gather(
            fetch_openf1(f"https://api.openf1.org/v1/laps?session_key={session_key}&driver_number={d1_num}"),
            fetch_openf1(f"https://api.openf1.org/v1/laps?session_key={session_key}&driver_number={d2_num}")
        )

        def find_fastest(laps_raw, code):
            valid = [l for l in (laps_raw or []) if l.get("lap_duration") and not l.get("is_pit_out_lap")]
            if not valid:
                raise HTTPException(status_code=404, detail=f"No valid laps for {code}")
            return min(valid, key=lambda l: l["lap_duration"])

        lap1 = find_fastest(laps1_raw, driver1)
        lap2 = find_fastest(laps2_raw, driver2)

        # 4. Fetch car data for each fastest lap in parallel
        def lap_time_range(lap):
            start_str = lap["date_start"]
            start = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
            end = start + timedelta(seconds=float(lap["lap_duration"]) + 2)
            end_str = end.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
            return start_str, end_str

        d1_start, d1_end = lap_time_range(lap1)
        d2_start, d2_end = lap_time_range(lap2)

        car1_raw, car2_raw = await asyncio.gather(
            fetch_openf1(f"https://api.openf1.org/v1/car_data?session_key={session_key}&driver_number={d1_num}&date>={d1_start}&date<={d1_end}"),
            fetch_openf1(f"https://api.openf1.org/v1/car_data?session_key={session_key}&driver_number={d2_num}&date>={d2_start}&date<={d2_end}")
        )

        if not car1_raw:
            raise HTTPException(status_code=404, detail=f"No car telemetry for {driver1}")
        if not car2_raw:
            raise HTTPException(status_code=404, detail=f"No car telemetry for {driver2}")

        # 5. Build DataFrames and compute cumulative distance from speed integration
        def build_telemetry_df(car_data):
            df = pd.DataFrame(car_data)
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True)
            df["dt"] = df["date"].diff().dt.total_seconds().fillna(0)
            for col in ["speed", "throttle", "brake", "n_gear"]:
                if col not in df.columns:
                    df[col] = 0
            df["distance"] = (df["speed"] * (1000 / 3600) * df["dt"]).cumsum()
            return df

        df1 = build_telemetry_df(car1_raw)
        df2 = build_telemetry_df(car2_raw)

        # 6. Driver colors
        color1 = DRIVER_COLORS.get(driver1, "#FF1801")
        color2 = DRIVER_COLORS.get(driver2, "#00E1D9")

        # 7. Stats
        stats1 = {
            "maxSpeed": float(df1["speed"].max()),
            "avgSpeed": float(df1["speed"].mean()),
            "maxThrottle": float(df1["throttle"].max()),
            "avgThrottle": float(df1["throttle"].mean()),
        }
        stats2 = {
            "maxSpeed": float(df2["speed"].max()),
            "avgSpeed": float(df2["speed"].mean()),
            "maxThrottle": float(df2["throttle"].max()),
            "avgThrottle": float(df2["throttle"].mean()),
        }

        # 8. Interpolate both drivers onto 400-point common distance grid
        max_dist = min(float(df1["distance"].max()), float(df2["distance"].max()))
        common_grid = np.linspace(0, max_dist, num=400)

        def interp(df, col):
            return np.interp(common_grid, df["distance"].values, df[col].values)

        s1, t1, b1, g1 = interp(df1, "speed"), interp(df1, "throttle"), interp(df1, "brake"), interp(df1, "n_gear")
        s2, t2, b2, g2 = interp(df2, "speed"), interp(df2, "throttle"), interp(df2, "brake"), interp(df2, "n_gear")

        data1 = [{"distance": float(d), "speed": float(s), "throttle": float(t), "brake": bool(b > 0.5), "gear": int(round(g))}
                 for d, s, t, b, g in zip(common_grid, s1, t1, b1, g1)]
        data2 = [{"distance": float(d), "speed": float(s), "throttle": float(t), "brake": bool(b > 0.5), "gear": int(round(g))}
                 for d, s, t, b, g in zip(common_grid, s2, t2, b2, g2)]

        return {
            "year": year,
            "location": location,
            "driver1": {
                "code": driver1,
                "color": color1,
                "fullName": d1_fullname,
                "lapTime": format_lap_time_seconds(lap1["lap_duration"]),
                "stats": stats1,
                "telemetry": data1
            },
            "driver2": {
                "code": driver2,
                "color": color2,
                "fullName": d2_fullname,
                "lapTime": format_lap_time_seconds(lap2["lap_duration"]),
                "stats": stats2,
                "telemetry": data2
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
