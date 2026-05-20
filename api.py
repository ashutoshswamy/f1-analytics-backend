import os
import gc
import json
import asyncio
import urllib.request
import ssl
from datetime import datetime, timezone
from functools import lru_cache
import pandas as pd
import numpy as np
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
import fastf1
import fastf1.plotting


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
    # Never load telemetry=True globally — too heavy for 512MB RAM.
    # Telemetry is fetched per-lap via lap.get_car_data() instead.
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

def get_ssl_context():
    return ssl._create_unverified_context()

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

        session = await _load_session(year, location, "R")

        # Pick fastest laps
        lap1 = session.laps.pick_drivers(driver1).pick_fastest()
        lap2 = session.laps.pick_drivers(driver2).pick_fastest()

        if lap1 is None or len(lap1) == 0:
            raise HTTPException(status_code=404, detail=f"No telemetry laps found for driver {driver1}")
        if lap2 is None or len(lap2) == 0:
            raise HTTPException(status_code=404, detail=f"No telemetry laps found for driver {driver2}")

        tel1 = lap1.get_car_data().add_distance()
        tel2 = lap2.get_car_data().add_distance()

        # Try to fetch team colors
        try:
            color1 = fastf1.plotting.get_driver_color(driver1, session=session)
            if not color1 or not isinstance(color1, str) or len(color1) < 3:
                color1 = "#FF1801"
        except Exception:
            color1 = "#FF1801" # Default F1 Red
        try:
            color2 = fastf1.plotting.get_driver_color(driver2, session=session)
            if not color2 or not isinstance(color2, str) or len(color2) < 3:
                color2 = "#00E1D9"
        except Exception:
            color2 = "#00E1D9" # Contrast Cyan

        # Defensive padding: Ensure required columns exist
        for col in ["Speed", "Throttle", "Brake", "Gear"]:
            if col not in tel1.columns:
                tel1[col] = 0
            if col not in tel2.columns:
                tel2[col] = 0

        # Calculate driver comparison statistics
        stats1 = {
            "maxSpeed": float(tel1["Speed"].max()) if not tel1.empty else 0.0,
            "avgSpeed": float(tel1["Speed"].mean()) if not tel1.empty else 0.0,
            "maxThrottle": float(tel1["Throttle"].max()) if not tel1.empty else 0.0,
            "avgThrottle": float(tel1["Throttle"].mean()) if not tel1.empty else 0.0,
        }
        stats2 = {
            "maxSpeed": float(tel2["Speed"].max()) if not tel2.empty else 0.0,
            "avgSpeed": float(tel2["Speed"].mean()) if not tel2.empty else 0.0,
            "maxThrottle": float(tel2["Throttle"].max()) if not tel2.empty else 0.0,
            "avgThrottle": float(tel2["Throttle"].mean()) if not tel2.empty else 0.0,
        }

        # Align both driver datasets to a common grid via linear interpolation
        max_dist = min(tel1["Distance"].max(), tel2["Distance"].max())
        common_grid = np.linspace(0, max_dist, num=400)

        speed1_interp = np.interp(common_grid, tel1["Distance"], tel1["Speed"])
        throttle1_interp = np.interp(common_grid, tel1["Distance"], tel1["Throttle"])
        brake1_interp = np.interp(common_grid, tel1["Distance"], tel1["Brake"])
        gear1_interp = np.interp(common_grid, tel1["Distance"], tel1["Gear"])

        speed2_interp = np.interp(common_grid, tel2["Distance"], tel2["Speed"])
        throttle2_interp = np.interp(common_grid, tel2["Distance"], tel2["Throttle"])
        brake2_interp = np.interp(common_grid, tel2["Distance"], tel2["Brake"])
        gear2_interp = np.interp(common_grid, tel2["Distance"], tel2["Gear"])

        data1 = []
        data2 = []
        for i, dist in enumerate(common_grid):
            data1.append({
                "distance": float(dist),
                "speed": float(speed1_interp[i]),
                "throttle": float(throttle1_interp[i]),
                "brake": bool(brake1_interp[i] > 0.5),
                "gear": int(round(gear1_interp[i]))
            })
            data2.append({
                "distance": float(dist),
                "speed": float(speed2_interp[i]),
                "throttle": float(throttle2_interp[i]),
                "brake": bool(brake2_interp[i] > 0.5),
                "gear": int(round(gear2_interp[i]))
            })

        return {
            "year": year,
            "location": location,
            "driver1": {
                "code": driver1,
                "color": color1,
                "fullName": lap1.get("Driver", driver1),
                "lapTime": format_lap_time_td(lap1.get("LapTime")),
                "stats": stats1,
                "telemetry": data1
            },
            "driver2": {
                "code": driver2,
                "color": color2,
                "fullName": lap2.get("Driver", driver2),
                "lapTime": format_lap_time_td(lap2.get("LapTime")),
                "stats": stats2,
                "telemetry": data2
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
