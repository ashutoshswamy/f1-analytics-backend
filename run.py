import asyncio
import os
import uvicorn
from telegram.ext import Application, CommandHandler
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Import bot command handlers
import bot
# Import FastAPI application
from api import app

async def run_both():
    # 1. Initialize Telegram Bot
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token or token == "YOUR_TELEGRAM_BOT_TOKEN":
        print("WARNING: TELEGRAM_BOT_TOKEN is not set correctly. Bot will not run.")
        bot_app = None
    else:
        print("Initializing Telegram F1 Analytics Bot...")
        bot_app = Application.builder().token(token).build()
        
        # Register all Command Handlers from bot.py
        bot_app.add_handler(CommandHandler("start", bot.start))
        bot_app.add_handler(CommandHandler("help", bot.help_command))
        bot_app.add_handler(CommandHandler("speed", bot.speed_compare))
        bot_app.add_handler(CommandHandler("schedule", bot.get_schedule))
        bot_app.add_handler(CommandHandler("results", bot.race_results))
        bot_app.add_handler(CommandHandler("driver_standings", bot.get_driver_standings))
        bot_app.add_handler(CommandHandler("team_standings", bot.get_team_standings))
        bot_app.add_handler(CommandHandler("last_race", bot.last_race_results))
        bot_app.add_handler(CommandHandler("driver", bot.driver_info))
        bot_app.add_handler(CommandHandler("quali", bot.qualifying_results))
        bot_app.add_handler(CommandHandler("track", bot.track_info))
        bot_app.add_handler(CommandHandler("next_race", bot.next_race))
        bot_app.add_handler(CommandHandler("compare", bot.driver_comparison))

        # Start the bot asynchronously
        await bot_app.initialize()
        await bot_app.start()
        await bot_app.updater.start_polling()
        print("Telegram F1 Analytics Bot is running...")

    # 2. Configure and run FastAPI with Uvicorn
    # We run it on host 0.0.0.0 and port 8000
    print("Starting FastAPI Uvicorn Server on http://localhost:8000...")
    config = uvicorn.Config(app, host="0.0.0.0", port=8000, loop="asyncio")
    server = uvicorn.Server(config)
    
    # Run uvicorn server in the same loop
    await server.serve()

    # 3. Clean shutdown if the server is stopped
    if bot_app:
        print("Stopping Telegram F1 Analytics Bot...")
        await bot_app.updater.stop()
        await bot_app.stop()
        await bot_app.shutdown()
        print("Bot stopped successfully.")

if __name__ == "__main__":
    try:
        asyncio.run(run_both())
    except KeyboardInterrupt:
        print("Services stopped by user.")
