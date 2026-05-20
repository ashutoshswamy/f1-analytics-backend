# F1 Analytics — Backend

> **Repo:** [ashutoshswamy/f1-analytics-backend](https://github.com/ashutoshswamy/f1-analytics-backend)  
> **Frontend:** [ashutoshswamy/f1-analytics](https://github.com/ashutoshswamy/f1-analytics)

Python backend that runs two services concurrently via `run.py`:

- **FastAPI** REST server on port `8000` — consumed by the Next.js frontend
- **Telegram bot** — polling-based, handles chat commands directly

## Stack

- [FastF1](https://github.com/theOehrly/FastF1) — telemetry & session data
- [FastAPI](https://fastapi.tiangolo.com) + [Uvicorn](https://www.uvicorn.org) — REST API
- [python-telegram-bot](https://python-telegram-bot.org) v20+ — async bot framework
- [Jolpica Ergast API](https://api.jolpi.ca) — standings, schedules, driver info

## Setup

```bash
git clone https://github.com/ashutoshswamy/f1-analytics-backend
cd f1-analytics-backend
python -m venv venv
source venv/bin/activate    # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Configure `.env`:

```ini
TELEGRAM_BOT_TOKEN=123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ
```

Get a token from [@BotFather](https://t.me/BotFather) on Telegram.

## Running

```bash
python run.py
```

Starts both the FastAPI server and Telegram bot in the same async event loop. Stop with `Ctrl+C`.

To run the bot only (no API server):

```bash
python bot.py
```

## API Endpoints

All endpoints are prefixed with `/api`.

| Method | Endpoint | Params | Description |
|--------|----------|--------|-------------|
| GET | `/api/health` | — | Health check — returns status and UTC timestamp |
| GET | `/api/next_race` | — | Next GP countdown + weekend schedule |
| GET | `/api/last_race` | — | Most recent race top 10 |
| GET | `/api/schedule` | `year` (optional) | Full season calendar |
| GET | `/api/results` | `year`, `location` | Race top 10 with points |
| GET | `/api/quali` | `year`, `location` | Qualifying top 10 (Q1/Q2/Q3) |
| GET | `/api/standings/drivers` | `year` (optional) | Driver championship standings |
| GET | `/api/standings/teams` | `year` (optional) | Constructor championship standings |
| GET | `/api/driver` | `code` | Driver profile (e.g. `HAM`) |
| GET | `/api/track` | `year`, `location` | Circuit metadata + coordinates |
| GET | `/api/telemetry` | `year`, `location`, `driver1`, `driver2` | Interpolated fastest-lap telemetry for two drivers |

Interactive docs available at `http://localhost:8000/docs`.

## Telegram Bot Commands

| Command | Usage | Description |
|---------|-------|-------------|
| `/start` | `/start` | Welcome message + command list |
| `/help` | `/help` | Detailed usage guide |
| `/speed` | `/speed 2023 Monza VER HAM` | Fastest lap speed chart (image) |
| `/compare` | `/compare 2023 Monza LEC SAI` | Head-to-head race stats |
| `/results` | `/results 2023 Monza` | Top 10 race results |
| `/quali` | `/quali 2023 Spa` | Top 10 qualifying times |
| `/last_race` | `/last_race` | Most recent GP results |
| `/next_race` | `/next_race` | Countdown to next GP |
| `/schedule` | `/schedule 2024` | Season calendar |
| `/driver_standings` | `/driver_standings 2024` | Driver championship table |
| `/team_standings` | `/team_standings 2024` | Constructor championship table |
| `/driver` | `/driver VER` | Driver profile card |
| `/track` | `/track 2023 Monza` | Circuit info card |

## Caching

FastF1 caches session data in `f1_cache/` (gitignored). First query per session downloads ~20–50 MB; subsequent queries are instant.
