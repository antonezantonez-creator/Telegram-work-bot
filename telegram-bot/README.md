# Telegram Bot (aiogram)

A Telegram shift-income bot built with aiogram 3. Workers can record day or
night shifts, add a second position, and view a daily and monthly income report.

## Run in Replit

The bot token is stored as the `TELEGRAM_BOT_TOKEN` Replit Secret. Start the
**Telegram Bot** workflow to connect the bot to Telegram.

## Run locally

1. Install Python 3.11 or newer.
2. From this directory, install the project dependencies:

   ```bash
   python -m pip install -e .
   ```

3. Copy `.env.example` to `.env` and add your bot token.
4. Start the bot:

   ```bash
   python -m telegram_bot
   ```

Create a bot and obtain its token through Telegram's BotFather. Never commit a
real token or share it in chat.

## Shift entry and calculation

Each shift records a type (day/night) and date. Date entry defaults to today,
with an option to enter a past date as `DD.MM.YYYY`. A shift can include a
primary position and one optional combining position. The combining position's
normal earnings are multiplied by 70%.

The three coefficients in list-valued positions map to lines 1, 2, and 3 in
order. Line 1 quantities are entered as pieces/trays. Lines 2 and 3 are entered
as pallets and converted using 2,675 trays per pallet. Income is calculated as
the coefficient multiplied by the converted tray count and rounded to two
decimal places.

Completed shifts are stored in `data/shifts.sqlite3`, separated by Telegram
user ID. The database is excluded from Git. Startup migrates records from the
previous schema, preserving their saved amounts and assigning them their
original local date and a day-shift type.

## Add features

Add new aiogram handlers in `main.py` or split them into separate modules as the
bot grows. Keep secrets and environment-specific settings out of source code.
