import asyncio
import html
import logging
import os
import sqlite3
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from dotenv import load_dotenv

from telegram_bot.config import Settings


# List values are ordered by production line: line 1, line 2, then line 3.
POSITIONS: dict[str, list[float] | dict[int, float]] = {
    "Мастер контрольный 1р": [0.04011, 0.03865, 0.03175],
    "Мастер контрольный 2р": [0.04719, 0.04547, 0.03735],
    "ПМК 1р": [0.03426, 0.03302, 0.02712],
    "ПМК 2р": [0.03652, 0.03519, 0.02891],
    "РККМ линия 1": {1: 0.09090},
    "РККМ линия 2": {2: 0.07490},
    "РККМ линия 3": {3: 0.06460},
    "МВФМ линия 1": {1: 0.09003},
    "МВФМ линия 2": {2: 0.08721},
    "МВФМ линия 3": {3: 0.07164},
    "Помощник МВФМ": {3: 0.06191},
    "УУ линия 1": {1: 0.07343},
    "УУ линия 2": {2: 0.07078},
    "УУ линия 3": {3: 0.05845},
}

TRAYS_PER_PALLET = 2675
COMBINING_PERCENT = 70
TIME_ZONE = ZoneInfo("Europe/Moscow")
DEFAULT_DATABASE_PATH = Path(__file__).parent / "data" / "shifts.sqlite3"
# Set ADMIN_ID in the workspace environment to enable the admin panel.
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

ADD_SHIFT_BUTTON = "➕ Внести смену"
INCOME_BUTTON = "📊 Мой доход"
DELETE_LAST_SHIFT_BUTTON = "❌ Удалить последнюю смену"
ADMIN_PANEL_BUTTON = "👑 Панель мастера"
CLEAR_TEST_DATA_BUTTON = "🧹 Очистить тестовые данные"
CONFIRM_DELETE_LAST_BUTTON = "🗑 Да, удалить эту смену"
CONFIRM_CLEAR_ALL_BUTTON = "🧹 Да, удалить все смены"
CONFIRM_CANCEL_BUTTON = "↩️ Оставить данные"
DAY_SHIFT_BUTTON = "☀️ Дневная"
NIGHT_SHIFT_BUTTON = "🌙 Ночная"
TODAY_BUTTON = "📅 Сегодня (Today)"
YESTERDAY_BUTTON = "📅 Вчера (Yesterday)"
ENTER_DATE_BUTTON = "✍️ Другая дата"
REPEAT_LAST_SHIFT_BUTTON = "🔁 Повторить прошлую смену"
ADD_COMBINING_BUTTON = "➕ Добавить совмещение"
NO_COMBINING_BUTTON = "Без совмещения"
SKIP_BUTTON = "Пропустить"
CONFIRM_BUTTON = "✅ Сохранить смену"
CANCEL_BUTTON = "❌ Отмена"

SHIFT_TYPES = {
    DAY_SHIFT_BUTTON: "day",
    NIGHT_SHIFT_BUTTON: "night",
}
SHIFT_TYPE_LABELS = {
    "day": DAY_SHIFT_BUTTON,
    "night": NIGHT_SHIFT_BUTTON,
}
MONTH_NAMES = (
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
)
REPORT_MESSAGE_LIMIT = 3500
ADMIN_REPORT_MESSAGE_LIMIT = 3500
REPORT_SEPARATOR = "━━━━━━━━━━━━━━━━━━━"

router = Router(name="telegram-bot")


class ShiftEntry(StatesGroup):
    choosing_shift_type = State()
    choosing_date = State()
    entering_date = State()
    choosing_primary_position = State()
    entering_primary_quantity = State()
    choosing_combining = State()
    choosing_combining_position = State()
    entering_combining_quantity = State()
    confirming = State()


def database_path() -> Path:
    configured_path = os.getenv("BOT_DATABASE_PATH")
    return Path(configured_path) if configured_path else DEFAULT_DATABASE_PATH


def make_keyboard(
    labels: list[str],
    *,
    columns: int = 2,
    one_time: bool = True,
) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=label) for label in labels[index : index + columns]]
        for index in range(0, len(labels), columns)
    ]
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        one_time_keyboard=one_time,
    )


def is_admin(telegram_user_id: int | None) -> bool:
    return bool(ADMIN_ID and telegram_user_id == ADMIN_ID)


def main_menu_keyboard(telegram_user_id: int | None = None) -> ReplyKeyboardMarkup:
    buttons = [ADD_SHIFT_BUTTON, INCOME_BUTTON, DELETE_LAST_SHIFT_BUTTON]
    if is_admin(telegram_user_id):
        buttons.append(ADMIN_PANEL_BUTTON)
    return make_keyboard(buttons, columns=2, one_time=False)


def admin_panel_keyboard(telegram_user_id: int) -> ReplyKeyboardMarkup:
    if not is_admin(telegram_user_id):
        return main_menu_keyboard(telegram_user_id)
    return make_keyboard(
        [
            CLEAR_TEST_DATA_BUTTON,
            ADD_SHIFT_BUTTON,
            INCOME_BUTTON,
            DELETE_LAST_SHIFT_BUTTON,
            ADMIN_PANEL_BUTTON,
        ],
        columns=2,
        one_time=False,
    )


def menu_keyboard_for(message: Message) -> ReplyKeyboardMarkup:
    user_id = message.from_user.id if message.from_user else None
    return main_menu_keyboard(user_id)


def position_keyboard(
    exclude: str | None = None,
    *,
    include_repeat: bool = False,
) -> ReplyKeyboardMarkup:
    positions = [position for position in POSITIONS if position != exclude]
    if include_repeat:
        positions.insert(0, REPEAT_LAST_SHIFT_BUTTON)
    return make_keyboard(positions + [CANCEL_BUTTON])


def date_selection_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=TODAY_BUTTON,
                    callback_data="shift_date:today",
                ),
                InlineKeyboardButton(
                    text=YESTERDAY_BUTTON,
                    callback_data="shift_date:yesterday",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=ENTER_DATE_BUTTON,
                    callback_data="shift_date:custom",
                )
            ],
            [
                InlineKeyboardButton(
                    text=CANCEL_BUTTON,
                    callback_data="shift_date:cancel",
                )
            ],
        ]
    )


def last_shift_delete_confirmation_keyboard(shift_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=CONFIRM_DELETE_LAST_BUTTON,
                    callback_data=f"delete_last:confirm:{shift_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=CONFIRM_CANCEL_BUTTON,
                    callback_data="delete_last:cancel",
                )
            ],
        ]
    )


def clear_all_confirmation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=CONFIRM_CLEAR_ALL_BUTTON,
                    callback_data="admin_clear:confirm",
                )
            ],
            [
                InlineKeyboardButton(
                    text=CONFIRM_CANCEL_BUTTON,
                    callback_data="admin_clear:cancel",
                )
            ],
        ]
    )


def quantity_keyboard() -> ReplyKeyboardMarkup:
    return make_keyboard([SKIP_BUTTON, CANCEL_BUTTON])


def cancel_keyboard() -> ReplyKeyboardMarkup:
    return make_keyboard([CANCEL_BUTTON])


def confirmation_keyboard() -> ReplyKeyboardMarkup:
    return make_keyboard([CONFIRM_BUTTON, CANCEL_BUTTON])


def available_lines(position: str) -> list[int]:
    rates = POSITIONS[position]
    return [1, 2, 3] if isinstance(rates, list) else sorted(rates)


def coefficient_for(position: str, line: int) -> float:
    rates = POSITIONS[position]
    return rates[line - 1] if isinstance(rates, list) else rates[line]


def line_unit(line: int) -> str:
    return "штук" if line == 1 else "паллет"


def quantity_to_trays(line: int, quantity: int) -> int:
    return quantity if line == 1 else quantity * TRAYS_PER_PALLET


def calculate_income_cents(position: str, line: int, quantity: int) -> int:
    trays = quantity_to_trays(line, quantity)
    amount = Decimal(str(coefficient_for(position, line))) * trays
    rounded_amount = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return int(rounded_amount * 100)


def apply_combining_rate(normal_income_cents: int) -> int:
    adjusted = Decimal(normal_income_cents) * Decimal(COMBINING_PERCENT) / 100
    return int(adjusted.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def format_money(cents: int) -> str:
    value = f"{Decimal(cents) / 100:,.2f}"
    return value.replace(",", "\u00a0").replace(".", ",")


def format_report_money(cents: int) -> str:
    value = f"{Decimal(cents) / 100:,.2f}"
    return f"{value.replace(',', ' ')} ₽"


def format_report_quantity(quantity: int) -> str:
    return f"{quantity:,}".replace(",", " ")


def parse_shift_date(value: str) -> date:
    parsed_date = None
    for date_format in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            parsed_date = datetime.strptime(value.strip(), date_format).date()
            break
        except ValueError:
            continue

    if parsed_date is None:
        raise ValueError("Введите дату в формате ДД.ММ.ГГГГ, например 21.10.2026.")
    if parsed_date > datetime.now(TIME_ZONE).date():
        raise ValueError("Нельзя указать будущую дату смены.")
    return parsed_date


def _ensure_column(
    connection: sqlite3.Connection,
    table: str,
    column: str,
    definition: str,
) -> None:
    existing_columns = {
        row["name"]
        for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column not in existing_columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def initialize_database() -> None:
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS shifts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_user_id INTEGER NOT NULL,
                worker_name TEXT NOT NULL DEFAULT '',
                position TEXT NOT NULL,
                created_at TEXT NOT NULL,
                total_income_cents INTEGER NOT NULL,
                shift_date TEXT NOT NULL DEFAULT '',
                shift_type TEXT NOT NULL DEFAULT 'day',
                combining_position TEXT,
                primary_income_cents INTEGER NOT NULL DEFAULT 0,
                combining_base_income_cents INTEGER NOT NULL DEFAULT 0,
                combining_income_cents INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS shift_lines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                shift_id INTEGER NOT NULL REFERENCES shifts(id) ON DELETE CASCADE,
                line_number INTEGER NOT NULL,
                input_quantity INTEGER NOT NULL,
                input_unit TEXT NOT NULL,
                tray_count INTEGER NOT NULL,
                coefficient REAL NOT NULL,
                income_cents INTEGER NOT NULL,
                role TEXT NOT NULL DEFAULT 'primary'
                    CHECK (role IN ('primary', 'combining'))
            )
            """
        )

        # Upgrade databases created by earlier versions of this bot.
        _ensure_column(
            connection, "shifts", "worker_name", "TEXT NOT NULL DEFAULT ''"
        )
        _ensure_column(connection, "shifts", "shift_date", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(
            connection, "shifts", "shift_type", "TEXT NOT NULL DEFAULT 'day'"
        )
        _ensure_column(connection, "shifts", "combining_position", "TEXT")
        _ensure_column(
            connection,
            "shifts",
            "primary_income_cents",
            "INTEGER NOT NULL DEFAULT 0",
        )
        _ensure_column(
            connection,
            "shifts",
            "combining_base_income_cents",
            "INTEGER NOT NULL DEFAULT 0",
        )
        _ensure_column(
            connection,
            "shifts",
            "combining_income_cents",
            "INTEGER NOT NULL DEFAULT 0",
        )
        _ensure_column(
            connection, "shift_lines", "role", "TEXT NOT NULL DEFAULT 'primary'"
        )

        connection.execute(
            """
            UPDATE shifts
            SET shift_date = substr(created_at, 1, 10)
            WHERE shift_date = ''
            """
        )
        connection.execute(
            """
            UPDATE shifts
            SET primary_income_cents = total_income_cents
            WHERE primary_income_cents = 0 AND total_income_cents != 0
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS shifts_user_date_idx "
            "ON shifts (telegram_user_id, shift_date)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS shift_lines_shift_role_idx "
            "ON shift_lines (shift_id, role)"
        )


def normalize_entries(
    position: str,
    entries: list[dict[str, int]],
) -> list[dict[str, int | float]]:
    if position not in POSITIONS:
        raise ValueError("Unknown position.")

    allowed_lines = set(available_lines(position))
    seen_lines: set[int] = set()
    normalized: list[dict[str, int | float]] = []

    for entry in entries:
        line = int(entry["line"])
        quantity = int(entry["quantity"])
        if line not in allowed_lines or line in seen_lines or quantity <= 0:
            raise ValueError("Invalid shift line data.")
        seen_lines.add(line)
        normalized.append(
            {
                "line": line,
                "quantity": quantity,
                "trays": quantity_to_trays(line, quantity),
                "coefficient": coefficient_for(position, line),
                "income_cents": calculate_income_cents(position, line, quantity),
            }
        )

    return normalized


def save_shift(
    telegram_user_id: int,
    worker_name: str,
    shift_date: str,
    shift_type: str,
    primary_position: str,
    primary_entries: list[dict[str, int]],
    combining_position: str | None,
    combining_entries: list[dict[str, int]],
) -> int:
    parsed_date = date.fromisoformat(shift_date)
    if shift_type not in {"day", "night"}:
        raise ValueError("Invalid shift type.")
    if parsed_date > datetime.now(TIME_ZONE).date():
        raise ValueError("Shift date cannot be in the future.")
    if telegram_user_id <= 0:
        raise ValueError("Invalid Telegram user ID.")

    primary_rows = normalize_entries(primary_position, primary_entries)
    if not primary_rows:
        raise ValueError("A shift must include at least one primary quantity.")

    combining_rows: list[dict[str, int | float]] = []
    if combining_position is not None:
        if combining_position == primary_position:
            raise ValueError("Combining position must differ from the primary one.")
        combining_rows = normalize_entries(combining_position, combining_entries)
        if not combining_rows:
            combining_position = None
    elif combining_entries:
        raise ValueError("Combining entries require a combining position.")

    primary_total = sum(int(row["income_cents"]) for row in primary_rows)
    combining_base_total = sum(int(row["income_cents"]) for row in combining_rows)
    combining_total = apply_combining_rate(combining_base_total)
    total_income = primary_total + combining_total
    created_at = datetime.now(TIME_ZONE).isoformat(timespec="seconds")

    with sqlite3.connect(database_path()) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        cursor = connection.execute(
            """
            INSERT INTO shifts (
                telegram_user_id, worker_name, position, created_at, total_income_cents,
                shift_date, shift_type, combining_position,
                primary_income_cents, combining_base_income_cents,
                combining_income_cents
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                telegram_user_id,
                worker_name[:120],
                primary_position,
                created_at,
                total_income,
                parsed_date.isoformat(),
                shift_type,
                combining_position,
                primary_total,
                combining_base_total,
                combining_total,
            ),
        )
        shift_id = int(cursor.lastrowid)

        for role, rows in (("primary", primary_rows), ("combining", combining_rows)):
            connection.executemany(
                """
                INSERT INTO shift_lines (
                    shift_id, line_number, input_quantity, input_unit,
                    tray_count, coefficient, income_cents, role
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        shift_id,
                        int(row["line"]),
                        int(row["quantity"]),
                        line_unit(int(row["line"])),
                        int(row["trays"]),
                        float(row["coefficient"]),
                        int(row["income_cents"]),
                        role,
                    )
                    for row in rows
                ],
            )

    return shift_id


def _shifts_with_lines(
    connection: sqlite3.Connection,
    shift_rows: list[sqlite3.Row],
) -> list[dict[str, object]]:
    if not shift_rows:
        return []

    shift_ids = [int(row["id"]) for row in shift_rows]
    line_rows = []
    for offset in range(0, len(shift_ids), 900):
        batch = shift_ids[offset : offset + 900]
        placeholders = ",".join("?" for _ in batch)
        line_rows.extend(
            connection.execute(
                f"""
                SELECT shift_id, line_number, input_quantity, input_unit, tray_count,
                       coefficient, income_cents, role
                FROM shift_lines
                WHERE shift_id IN ({placeholders})
                ORDER BY id
                """,
                batch,
            ).fetchall()
        )

    result: list[dict[str, object]] = []
    shifts_by_id: dict[int, dict[str, object]] = {}
    for row in shift_rows:
        shift = {
            key: row[key]
            for key in (
                "id",
                "telegram_user_id",
                "worker_name",
                "shift_date",
                "shift_type",
                "position",
                "combining_position",
                "primary_income_cents",
                "combining_base_income_cents",
                "combining_income_cents",
                "total_income_cents",
            )
        }
        shift["primary_lines"] = []
        shift["combining_lines"] = []
        shifts_by_id[int(row["id"])] = shift
        result.append(shift)

    for row in line_rows:
        shift = shifts_by_id[int(row["shift_id"])]
        role_key = "combining_lines" if row["role"] == "combining" else "primary_lines"
        lines = shift[role_key]
        lines.append(
            {
                "line": int(row["line_number"]),
                "quantity": int(row["input_quantity"]),
                "unit": str(row["input_unit"]),
                "trays": int(row["tray_count"]),
                "coefficient": float(row["coefficient"]),
                "income_cents": int(row["income_cents"]),
            }
        )

    return result


def current_month_shifts(telegram_user_id: int) -> list[dict[str, object]]:
    now = datetime.now(TIME_ZONE).date()
    month_start = now.replace(day=1)
    if now.month == 12:
        next_month = month_start.replace(year=now.year + 1, month=1)
    else:
        next_month = month_start.replace(month=now.month + 1)

    with sqlite3.connect(database_path()) as connection:
        connection.row_factory = sqlite3.Row
        shift_rows = connection.execute(
            """
            SELECT id, telegram_user_id, worker_name, shift_date, shift_type,
                   position, combining_position,
                   primary_income_cents, combining_base_income_cents,
                   combining_income_cents, total_income_cents
            FROM shifts
            WHERE telegram_user_id = ? AND shift_date >= ? AND shift_date < ?
            ORDER BY shift_date DESC, id DESC
            """,
            (telegram_user_id, month_start.isoformat(), next_month.isoformat()),
        ).fetchall()
        return _shifts_with_lines(connection, shift_rows)


def all_saved_shifts() -> list[dict[str, object]]:
    with sqlite3.connect(database_path()) as connection:
        connection.row_factory = sqlite3.Row
        shift_rows = connection.execute(
            """
            SELECT id, telegram_user_id, worker_name, shift_date, shift_type,
                   position, combining_position,
                   primary_income_cents, combining_base_income_cents,
                   combining_income_cents, total_income_cents
            FROM shifts
            ORDER BY telegram_user_id, shift_date DESC, id DESC
            """
        ).fetchall()
        return _shifts_with_lines(connection, shift_rows)


def last_saved_shift(telegram_user_id: int) -> dict[str, object] | None:
    with sqlite3.connect(database_path()) as connection:
        connection.row_factory = sqlite3.Row
        shift_row = connection.execute(
            """
            SELECT id, telegram_user_id, worker_name, shift_date, shift_type,
                   position, combining_position,
                   primary_income_cents, combining_base_income_cents,
                   combining_income_cents, total_income_cents
            FROM shifts
            WHERE telegram_user_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (telegram_user_id,),
        ).fetchall()
        shifts = _shifts_with_lines(connection, shift_row)
        return shifts[0] if shifts else None


def has_saved_shift(telegram_user_id: int) -> bool:
    with sqlite3.connect(database_path()) as connection:
        return (
            connection.execute(
                "SELECT 1 FROM shifts WHERE telegram_user_id = ? LIMIT 1",
                (telegram_user_id,),
            ).fetchone()
            is not None
        )


def delete_shift_for_user(telegram_user_id: int, shift_id: int) -> bool:
    with sqlite3.connect(database_path()) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        cursor = connection.execute(
            "DELETE FROM shifts WHERE id = ? AND telegram_user_id = ?",
            (shift_id, telegram_user_id),
        )
        return cursor.rowcount == 1


def count_saved_shifts() -> int:
    with sqlite3.connect(database_path()) as connection:
        row = connection.execute("SELECT COUNT(*) FROM shifts").fetchone()
        return int(row[0])


def delete_all_saved_shifts() -> int:
    with sqlite3.connect(database_path()) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("DELETE FROM shift_lines")
        cursor = connection.execute("DELETE FROM shifts")
        return cursor.rowcount


def line_description(entry: dict[str, int | float | str]) -> str:
    line = int(entry["line"])
    quantity = int(entry["quantity"])
    if line == 1:
        return f"Линия 1 · {quantity} шт."
    trays = int(entry["trays"])
    return (
        f"Линия {line} · {quantity} паллет × "
        f"{TRAYS_PER_PALLET:,}".replace(",", " ")
        + f" = {trays:,}".replace(",", " ")
        + " лотков"
    )


def preview_lines(
    position: str,
    entries: list[dict[str, int]],
    *,
    combining: bool = False,
) -> list[str]:
    rows = normalize_entries(position, entries)
    result = []
    for row in rows:
        detail = line_description(row)
        amount = format_money(int(row["income_cents"]))
        suffix = " · обычный расчёт до совмещения" if combining else ""
        result.append(f"• {detail} — {amount}{suffix}")
    return result


def report_line_description(line: dict[str, object]) -> str:
    line_number = int(line["line"])
    quantity = int(line["quantity"])
    if line_number == 1:
        return f"Линия 1: {format_report_quantity(quantity)} шт."
    trays = int(line["trays"])
    return (
        f"Линия {line_number}: {quantity} пал. "
        f"({format_report_quantity(trays)} шт.)"
    )


def format_report_shift(shift: dict[str, object]) -> str:
    is_day_shift = str(shift["shift_type"]) == "day"
    shift_icon = "☀️" if is_day_shift else "🌙"
    shift_label = "Дневная сфера" if is_day_shift else "Ночная сфера"
    primary_position = html.escape(str(shift["position"]))
    lines = [
        f"{shift_icon} <b>{shift_label}</b> | "
        f"👤 {primary_position} (Основная)"
    ]

    primary_lines = shift["primary_lines"]
    for line in primary_lines:
        lines.append(
            f"• {report_line_description(line)} — "
            f"<b>{format_report_money(int(line['income_cents']))}</b>"
        )

    combining_position = shift["combining_position"]
    if combining_position:
        lines.append(
            f"🤝 {html.escape(str(combining_position))} "
            f"(Совмещение, {COMBINING_PERCENT}%)"
        )
        for line in shift["combining_lines"]:
            lines.append(
                f"• {report_line_description(line)} — "
                f"<b>{format_report_money(int(line['income_cents']))}</b> "
                "<i>до применения 70%</i>"
            )
        lines.append(
            f"💸 <b>Совмещение 70%:</b> "
            f"<b>{format_report_money(int(shift['combining_income_cents']))}</b>"
        )

    lines.append(
        f"💰 <b>За смену:</b> "
        f"<b>{format_report_money(int(shift['total_income_cents']))}</b>"
    )
    return "\n".join(lines)


def month_label() -> str:
    now = datetime.now(TIME_ZONE)
    return f"{MONTH_NAMES[now.month - 1].capitalize()} {now.year}"


def monthly_report_chunks(shifts: list[dict[str, object]]) -> list[str]:
    total_income = sum(int(shift["total_income_cents"]) for shift in shifts)
    title = (
        f"📊 <b>Доход за {html.escape(month_label())}</b>\n"
        f"Смен: {len(shifts)} · Итого за месяц: "
        f"<b>{format_report_money(total_income)}</b>"
    )
    if not shifts:
        return [title + "\n\nПока нет сохранённых смен."]

    shifts_by_date: dict[str, list[dict[str, object]]] = {}
    for shift in shifts:
        shifts_by_date.setdefault(str(shift["shift_date"]), []).append(shift)

    chunks: list[str] = []
    current = title
    continuation = title + "\n\n<i>Продолжение отчёта</i>"
    for shift_date, date_shifts in shifts_by_date.items():
        daily_total = sum(
            int(shift["total_income_cents"]) for shift in date_shifts
        )
        day_heading = (
            f"\n\n{REPORT_SEPARATOR}\n"
            f"📅 <b>{date.fromisoformat(shift_date).strftime('%d.%m.%Y')}</b> "
            f"(Итого за день: <b>{format_report_money(daily_total)}</b>)"
        )
        shift_blocks = [format_report_shift(shift) for shift in date_shifts]
        day_footer = f"\n{REPORT_SEPARATOR}"
        full_day = (
            day_heading
            + "\n"
            + "\n\n".join(shift_blocks)
            + day_footer
        )

        if len(current) + len(full_day) <= REPORT_MESSAGE_LIMIT:
            current += full_day
            continue

        if current != title:
            chunks.append(current)
            current = continuation

        if len(current) + len(full_day) <= REPORT_MESSAGE_LIMIT:
            current += full_day
            continue

        current += day_heading
        for block in shift_blocks:
            block_text = f"\n\n{block}"
            if len(current) + len(block_text) + len(day_footer) > REPORT_MESSAGE_LIMIT:
                current += day_footer
                chunks.append(current)
                current = continuation + day_heading
            current += block_text
        current += day_footer

    if current:
        chunks.append(current)
    return chunks


def admin_report_chunks(shifts: list[dict[str, object]]) -> list[str]:
    total_income = sum(int(shift["total_income_cents"]) for shift in shifts)
    title = (
        "👑 <b>Сводка по всем сменам</b>\n"
        f"Смен: {len(shifts)} · Начислено: "
        f"<b>{format_report_money(total_income)}</b>"
    )
    if not shifts:
        return [title + "\n\nВ базе пока нет сохранённых смен."]

    chunks: list[str] = []
    current = title
    continuation = title + "\n\n<i>Продолжение сводки</i>"
    previous_worker_id: int | None = None
    previous_date: str | None = None
    active_worker_header = ""
    active_date_header = ""
    daily_summaries: dict[tuple[int, str], tuple[int, int]] = {}
    for item in shifts:
        key = (int(item["telegram_user_id"]), str(item["shift_date"]))
        count, amount = daily_summaries.get(key, (0, 0))
        daily_summaries[key] = (
            count + 1,
            amount + int(item["total_income_cents"]),
        )

    for shift in shifts:
        worker_id = int(shift["telegram_user_id"])
        worker_name = str(shift["worker_name"] or "").strip()
        if not worker_name:
            worker_name = f"Сотрудник {worker_id}"
        shift_date = str(shift["shift_date"])
        context = ""

        if worker_id != previous_worker_id:
            active_worker_header = (
                f"👷 <b>{html.escape(worker_name)}</b> · ID {worker_id}"
            )
            previous_worker_id = worker_id
            previous_date = None
        if shift_date != previous_date:
            daily_count, daily_total = daily_summaries[(worker_id, shift_date)]
            active_date_header = (
                f"📅 <b>{date.fromisoformat(shift_date).strftime('%d.%m.%Y')}</b>"
                f" · {daily_count} смен · {format_report_money(daily_total)}"
            )
            context = f"\n\n{active_worker_header}\n{active_date_header}"
            previous_date = shift_date

        shift_block = "\n" + format_report_shift(shift)
        addition = context + shift_block
        if len(current) + len(addition) > ADMIN_REPORT_MESSAGE_LIMIT:
            if current != title:
                chunks.append(current)
            current = (
                continuation
                + f"\n\n{active_worker_header}\n{active_date_header}"
                + shift_block
            )
        else:
            current += addition

    if current:
        chunks.append(current)
    return chunks


async def prompt_for_position(message: Message, state: FSMContext) -> None:
    await state.set_state(ShiftEntry.choosing_primary_position)
    user_id = message.from_user.id if message.from_user else None
    can_repeat = user_id is not None and has_saved_shift(user_id)
    await message.answer(
        "👷 <b>Выберите основную должность:</b>"
        + (
            "\nИли повторите предыдущую смену — должности и объёмы "
            "подставятся автоматически."
            if can_repeat
            else ""
        ),
        reply_markup=position_keyboard(include_repeat=can_repeat),
    )


async def start_line_collection(
    message: Message,
    state: FSMContext,
    *,
    role: str,
    position: str,
) -> None:
    await state.update_data(
        **{
            f"{role}_position": position,
            f"{role}_lines": available_lines(position),
            f"{role}_line_index": 0,
            f"{role}_entries": [],
        }
    )
    await state.set_state(
        ShiftEntry.entering_primary_quantity
        if role == "primary"
        else ShiftEntry.entering_combining_quantity
    )
    await show_next_line(message, state, role)


async def show_next_line(message: Message, state: FSMContext, role: str) -> None:
    data = await state.get_data()
    lines = data[f"{role}_lines"]
    line_index = data[f"{role}_line_index"]
    entries = data[f"{role}_entries"]

    if line_index >= len(lines):
        if role == "primary":
            if not entries:
                await state.clear()
                await message.answer(
                    "Смена не сохранена: укажите объём хотя бы по одной линии.",
                    reply_markup=menu_keyboard_for(message),
                )
                return
            await state.set_state(ShiftEntry.choosing_combining)
            await message.answer(
                "🤝 <b>Добавить совмещение?</b>\n"
                "Доход по второй должности будет рассчитан по ставке 70%.",
                reply_markup=make_keyboard(
                    [ADD_COMBINING_BUTTON, NO_COMBINING_BUTTON, CANCEL_BUTTON]
                ),
            )
            return

        if not entries:
            await state.update_data(combining_position=None, combining_entries=[])
        await show_confirmation(message, state)
        return

    line = int(lines[line_index])
    unit = line_unit(line)
    await message.answer(
        f"📦 <b>Линия {line}</b>\n"
        f"Введите количество {unit}."
        + (
            f" Одна паллета = {TRAYS_PER_PALLET:,} лотков.".replace(",", " ")
            if line != 1
            else " Для линии 1 вводится прямое количество штук."
        )
        + f"\nЕсли объёма нет, нажмите «{SKIP_BUTTON}».",
        reply_markup=quantity_keyboard(),
    )


async def advance_line(
    message: Message,
    state: FSMContext,
    role: str,
    quantity: int | None,
) -> None:
    data = await state.get_data()
    lines = data[f"{role}_lines"]
    line_index = data[f"{role}_line_index"]
    entries = data[f"{role}_entries"]

    if quantity is not None and quantity > 0:
        entries.append({"line": int(lines[line_index]), "quantity": quantity})

    await state.update_data(
        **{
            f"{role}_entries": entries,
            f"{role}_line_index": line_index + 1,
        }
    )
    await show_next_line(message, state, role)


async def show_confirmation(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    primary_position = data["primary_position"]
    primary_entries = normalize_entries(primary_position, data["primary_entries"])
    primary_total = sum(int(entry["income_cents"]) for entry in primary_entries)
    combining_position = data.get("combining_position")
    combining_entries = (
        normalize_entries(combining_position, data.get("combining_entries", []))
        if combining_position
        else []
    )
    combining_base = sum(
        int(entry["income_cents"]) for entry in combining_entries
    )
    combining_total = apply_combining_rate(combining_base)
    total = primary_total + combining_total

    shift_date = date.fromisoformat(data["shift_date"])
    details = [
        f"🧾 <b>Проверьте смену</b>",
        f"📅 {shift_date.strftime('%d.%m.%Y')} · "
        f"{html.escape(SHIFT_TYPE_LABELS[data['shift_type']])}",
        *(
            ["🔁 Повторены должности и объёмы из предыдущей смены."]
            if data.get("repeated_last_shift")
            else []
        ),
        f"\n👤 <b>Основная должность:</b> {html.escape(primary_position)}",
        *preview_lines(primary_position, data["primary_entries"]),
        f"Основной доход: <b>{format_money(primary_total)}</b>",
    ]
    if combining_position and combining_entries:
        details.extend(
            [
                f"\n🤝 <b>Совмещение:</b> {html.escape(combining_position)}",
                *preview_lines(
                    combining_position,
                    data["combining_entries"],
                    combining=True,
                ),
                f"Начисление: {format_money(combining_base)} × 70% = "
                f"<b>{format_money(combining_total)}</b>",
            ]
        )
    details.extend(
        [
            f"\n💰 <b>Итого за смену: {format_money(total)}</b>",
            "\nСохранить эту смену?",
        ]
    )
    await state.set_state(ShiftEntry.confirming)
    await message.answer(
        "\n".join(details),
        reply_markup=confirmation_keyboard(),
    )


@router.message(CommandStart())
async def start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "👋 <b>Здравствуйте!</b>\n"
        "Внесите смену или посмотрите доход за текущий месяц.",
        reply_markup=menu_keyboard_for(message),
    )


@router.message(Command("help"))
async def help_command(message: Message) -> None:
    await message.answer(
        f"➕ <b>{ADD_SHIFT_BUTTON}</b> — укажите тип и дату смены, "
        "должность и объём по линиям.\n"
        f"🤝 Можно добавить совмещение: вторая должность оплачивается по ставке "
        f"{COMBINING_PERCENT}%.\n"
        f"📊 <b>{INCOME_BUTTON}</b> — отчёт за текущий месяц с разбивкой по датам.\n\n"
        f"🔁 При добавлении смены можно повторить предыдущую запись.\n"
        f"Линия 1 вводится в штуках; линии 2 и 3 — в паллетах "
        f"(1 паллета = {TRAYS_PER_PALLET:,} лотков).\n"
        "Для отмены текущего ввода отправьте /cancel.",
        reply_markup=menu_keyboard_for(message),
    )


@router.message(Command("cancel"))
async def cancel_command(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "Ввод отменён. Можно начать заново из меню.",
        reply_markup=menu_keyboard_for(message),
    )


@router.message(StateFilter(None), F.text == ADD_SHIFT_BUTTON)
async def begin_shift(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.update_data(primary_entries=[], combining_entries=[])
    await state.set_state(ShiftEntry.choosing_shift_type)
    await message.answer(
        "🕒 <b>Выберите тип смены:</b>",
        reply_markup=make_keyboard(
            [DAY_SHIFT_BUTTON, NIGHT_SHIFT_BUTTON, CANCEL_BUTTON]
        ),
    )


@router.message(ShiftEntry.choosing_shift_type, F.text.in_(tuple(SHIFT_TYPES)))
async def choose_shift_type(message: Message, state: FSMContext) -> None:
    await state.update_data(shift_type=SHIFT_TYPES[message.text])
    await state.set_state(ShiftEntry.choosing_date)
    await message.answer(
        "📅 <b>Выберите дату смены.</b>\n"
        "Можно выбрать сегодня, вчера или указать дату вручную.",
        reply_markup=date_selection_keyboard(),
    )


@router.message(ShiftEntry.choosing_shift_type, F.text == CANCEL_BUTTON)
async def cancel_shift_type(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Ввод отменён.", reply_markup=menu_keyboard_for(message))


@router.message(ShiftEntry.choosing_shift_type)
async def invalid_shift_type(message: Message) -> None:
    await message.answer(
        "Выберите дневную или ночную смену кнопкой ниже.",
        reply_markup=make_keyboard(
            [DAY_SHIFT_BUTTON, NIGHT_SHIFT_BUTTON, CANCEL_BUTTON]
        ),
    )


@router.callback_query(ShiftEntry.choosing_date, F.data.startswith("shift_date:"))
async def choose_shift_date(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if not isinstance(callback.message, Message):
        return
    await callback.message.edit_reply_markup(reply_markup=None)

    selection = (callback.data or "").split(":", maxsplit=1)[-1]
    if selection == "cancel":
        await state.clear()
        await callback.message.answer(
            "Ввод отменён.",
            reply_markup=main_menu_keyboard(
                callback.from_user.id if callback.from_user else None
            ),
        )
        return
    if selection == "custom":
        await state.set_state(ShiftEntry.entering_date)
        await callback.message.answer(
            "Введите дату смены в формате <b>ДД.ММ.ГГГГ</b>, "
            "например 20.10.2026.\nМожно также отправить дату как ГГГГ-ММ-ДД.",
            reply_markup=cancel_keyboard(),
        )
        return

    today = datetime.now(TIME_ZONE).date()
    if selection == "today":
        selected_date = today
    elif selection == "yesterday":
        selected_date = today - timedelta(days=1)
    else:
        await callback.message.answer(
            "Выберите одну из дат кнопками ниже.",
            reply_markup=date_selection_keyboard(),
        )
        return

    await state.update_data(shift_date=selected_date.isoformat())
    await callback.message.answer(
        f"📅 Выбрана дата: <b>{selected_date.strftime('%d.%m.%Y')}</b>"
    )
    await prompt_for_position(callback.message, state)


@router.message(ShiftEntry.choosing_date, F.text == CANCEL_BUTTON)
async def cancel_date_selection(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Ввод отменён.", reply_markup=menu_keyboard_for(message))


@router.message(ShiftEntry.choosing_date)
async def invalid_date_choice(message: Message) -> None:
    await message.answer(
        "Выберите дату одной из кнопок ниже.",
        reply_markup=date_selection_keyboard(),
    )


@router.message(ShiftEntry.entering_date, F.text == CANCEL_BUTTON)
async def cancel_date_entry(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Ввод отменён.", reply_markup=menu_keyboard_for(message))


@router.message(ShiftEntry.entering_date, F.text)
async def enter_shift_date(message: Message, state: FSMContext) -> None:
    try:
        parsed_date = parse_shift_date(message.text or "")
    except ValueError as error:
        await message.answer(
            f"⚠️ {html.escape(str(error))}\n"
            "Попробуйте ещё раз или нажмите «Отмена».",
            reply_markup=cancel_keyboard(),
        )
        return

    await state.update_data(shift_date=parsed_date.isoformat())
    await prompt_for_position(message, state)


@router.message(ShiftEntry.entering_date)
async def invalid_date_input(message: Message) -> None:
    await message.answer("Отправьте дату текстом или нажмите «Отмена».")


@router.message(ShiftEntry.choosing_primary_position, F.text == CANCEL_BUTTON)
async def cancel_primary_position(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Ввод отменён.", reply_markup=menu_keyboard_for(message))


@router.message(ShiftEntry.choosing_primary_position, F.text)
async def choose_primary_position(message: Message, state: FSMContext) -> None:
    position = message.text or ""
    if position == REPEAT_LAST_SHIFT_BUTTON:
        if message.from_user is None:
            return
        previous_shift = last_saved_shift(message.from_user.id)
        if previous_shift is None:
            await message.answer(
                "Пока нет сохранённой смены для повтора.",
                reply_markup=position_keyboard(),
            )
            return

        primary_entries = [
            {"line": int(line["line"]), "quantity": int(line["quantity"])}
            for line in previous_shift["primary_lines"]
        ]
        if not primary_entries:
            await message.answer(
                "В прошлой смене нет объёмов для повтора. Выберите должность вручную.",
                reply_markup=position_keyboard(),
            )
            return
        combining_position = previous_shift["combining_position"]
        combining_entries = [
            {"line": int(line["line"]), "quantity": int(line["quantity"])}
            for line in previous_shift["combining_lines"]
        ]
        await state.update_data(
            primary_position=previous_shift["position"],
            primary_entries=primary_entries,
            combining_position=combining_position,
            combining_entries=combining_entries,
            repeated_last_shift=True,
        )
        await show_confirmation(message, state)
        return

    if position not in POSITIONS:
        await prompt_for_position(message, state)
        return
    await start_line_collection(
        message,
        state,
        role="primary",
        position=position,
    )


@router.message(ShiftEntry.choosing_primary_position)
async def invalid_primary_position(message: Message) -> None:
    await message.answer("Выберите должность текстовой кнопкой.")


@router.message(ShiftEntry.entering_primary_quantity, F.text == SKIP_BUTTON)
async def skip_primary_line(message: Message, state: FSMContext) -> None:
    await advance_line(message, state, "primary", None)


@router.message(ShiftEntry.entering_primary_quantity, F.text == CANCEL_BUTTON)
async def cancel_primary_quantity(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Ввод отменён.", reply_markup=menu_keyboard_for(message))


@router.message(ShiftEntry.entering_primary_quantity, F.text)
async def enter_primary_quantity(message: Message, state: FSMContext) -> None:
    try:
        quantity = int((message.text or "").strip())
        if quantity < 0:
            raise ValueError
    except ValueError:
        await message.answer(
            "Введите целое неотрицательное число или нажмите «Пропустить»."
        )
        return
    await advance_line(message, state, "primary", quantity)


@router.message(ShiftEntry.entering_primary_quantity)
async def invalid_primary_quantity(message: Message) -> None:
    await message.answer("Отправьте количество текстом или нажмите «Отмена».")


@router.message(ShiftEntry.choosing_combining, F.text == ADD_COMBINING_BUTTON)
async def begin_combining(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await state.set_state(ShiftEntry.choosing_combining_position)
    await message.answer(
        "🤝 <b>Выберите вторую должность для совмещения.</b>\n"
        f"Её доход будет уменьшен до {COMBINING_PERCENT}% от обычного расчёта.",
        reply_markup=position_keyboard(exclude=data["primary_position"]),
    )


@router.message(ShiftEntry.choosing_combining, F.text == NO_COMBINING_BUTTON)
async def skip_combining(message: Message, state: FSMContext) -> None:
    await state.update_data(combining_position=None, combining_entries=[])
    await show_confirmation(message, state)


@router.message(ShiftEntry.choosing_combining, F.text == CANCEL_BUTTON)
async def cancel_combining_choice(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Ввод отменён.", reply_markup=menu_keyboard_for(message))


@router.message(ShiftEntry.choosing_combining)
async def invalid_combining_choice(message: Message) -> None:
    await message.answer(
        "Добавьте совмещение или выберите «Без совмещения».",
        reply_markup=make_keyboard(
            [ADD_COMBINING_BUTTON, NO_COMBINING_BUTTON, CANCEL_BUTTON]
        ),
    )


@router.message(ShiftEntry.choosing_combining_position, F.text == CANCEL_BUTTON)
async def cancel_combining_position(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Ввод отменён.", reply_markup=menu_keyboard_for(message))


@router.message(ShiftEntry.choosing_combining_position, F.text)
async def choose_combining_position(message: Message, state: FSMContext) -> None:
    position = message.text or ""
    data = await state.get_data()
    if position not in POSITIONS or position == data["primary_position"]:
        await message.answer(
            "Выберите другую должность из списка или нажмите «Отмена».",
            reply_markup=position_keyboard(exclude=data["primary_position"]),
        )
        return
    await start_line_collection(
        message,
        state,
        role="combining",
        position=position,
    )


@router.message(ShiftEntry.choosing_combining_position)
async def invalid_combining_position(message: Message) -> None:
    await message.answer("Выберите должность текстовой кнопкой.")


@router.message(ShiftEntry.entering_combining_quantity, F.text == SKIP_BUTTON)
async def skip_combining_line(message: Message, state: FSMContext) -> None:
    await advance_line(message, state, "combining", None)


@router.message(ShiftEntry.entering_combining_quantity, F.text == CANCEL_BUTTON)
async def cancel_combining_quantity(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Ввод отменён.", reply_markup=menu_keyboard_for(message))


@router.message(ShiftEntry.entering_combining_quantity, F.text)
async def enter_combining_quantity(message: Message, state: FSMContext) -> None:
    try:
        quantity = int((message.text or "").strip())
        if quantity < 0:
            raise ValueError
    except ValueError:
        await message.answer(
            "Введите целое неотрицательное число или нажмите «Пропустить»."
        )
        return
    await advance_line(message, state, "combining", quantity)


@router.message(ShiftEntry.entering_combining_quantity)
async def invalid_combining_quantity(message: Message) -> None:
    await message.answer("Отправьте количество текстом или нажмите «Отмена».")


@router.message(ShiftEntry.confirming, F.text == CONFIRM_BUTTON)
async def confirm_shift(message: Message, state: FSMContext) -> None:
    if message.from_user is None:
        await state.clear()
        return

    data = await state.get_data()
    try:
        shift_id = save_shift(
            telegram_user_id=message.from_user.id,
            worker_name=(
                " ".join(
                    part
                    for part in (
                        message.from_user.first_name,
                        message.from_user.last_name,
                    )
                    if part
                )
                or (
                    f"@{message.from_user.username}"
                    if message.from_user.username
                    else f"Сотрудник {message.from_user.id}"
                )
            ),
            shift_date=data["shift_date"],
            shift_type=data["shift_type"],
            primary_position=data["primary_position"],
            primary_entries=data["primary_entries"],
            combining_position=data.get("combining_position"),
            combining_entries=data.get("combining_entries", []),
        )
    except (ValueError, sqlite3.Error):
        logging.exception("Failed to save shift")
        await message.answer(
            "⚠️ Не удалось сохранить смену. Данные не были подтверждены. "
            "Попробуйте ещё раз или начните ввод заново через /cancel.",
            reply_markup=confirmation_keyboard(),
        )
        return

    total_cents = sum(
        int(entry["income_cents"])
        for entry in normalize_entries(
            data["primary_position"], data["primary_entries"]
        )
    )
    if data.get("combining_position") and data.get("combining_entries"):
        combining_base = sum(
            int(entry["income_cents"])
            for entry in normalize_entries(
                data["combining_position"], data["combining_entries"]
            )
        )
        total_cents += apply_combining_rate(combining_base)

    await state.clear()
    await message.answer(
        f"✅ <b>Смена сохранена</b> · №{shift_id}\n"
        f"💰 Начисление за смену: <b>{format_money(total_cents)}</b>",
        reply_markup=menu_keyboard_for(message),
    )


@router.message(ShiftEntry.confirming, F.text == CANCEL_BUTTON)
async def cancel_shift_confirmation(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "Смена не сохранена.",
        reply_markup=menu_keyboard_for(message),
    )


@router.message(ShiftEntry.confirming)
async def invalid_confirmation(message: Message) -> None:
    await message.answer(
        f"Нажмите «{CONFIRM_BUTTON}» или «{CANCEL_BUTTON}».",
        reply_markup=confirmation_keyboard(),
    )


@router.message(StateFilter(None), F.text == INCOME_BUTTON)
async def show_income(message: Message) -> None:
    if message.from_user is None:
        return
    shifts = current_month_shifts(message.from_user.id)
    chunks = monthly_report_chunks(shifts)
    for index, chunk in enumerate(chunks):
        await message.answer(
            chunk,
            reply_markup=menu_keyboard_for(message)
            if index == len(chunks) - 1
            else None,
        )


@router.message(StateFilter(None), F.text == DELETE_LAST_SHIFT_BUTTON)
async def request_delete_last_shift(message: Message) -> None:
    if message.from_user is None:
        return
    shift = last_saved_shift(message.from_user.id)
    if shift is None:
        await message.answer(
            "У вас пока нет сохранённых смен.",
            reply_markup=menu_keyboard_for(message),
        )
        return

    shift_date = date.fromisoformat(str(shift["shift_date"])).strftime("%d.%m.%Y")
    shift_type = SHIFT_TYPE_LABELS.get(str(shift["shift_type"]), "Смена")
    position = html.escape(str(shift["position"]))
    amount = format_report_money(int(shift["total_income_cents"]))
    await message.answer(
        "⚠️ <b>Удалить последнюю сохранённую смену?</b>\n"
        f"📅 {shift_date} · {html.escape(shift_type)}\n"
        f"👤 {position}\n"
        f"💰 {amount}\n\n"
        "Удаление необратимо.",
        reply_markup=last_shift_delete_confirmation_keyboard(int(shift["id"])),
    )


@router.callback_query(F.data.startswith("delete_last:"))
async def confirm_delete_last_shift(callback: CallbackQuery) -> None:
    await callback.answer()
    if not isinstance(callback.message, Message):
        return
    await callback.message.edit_reply_markup(reply_markup=None)

    user_id = callback.from_user.id if callback.from_user else None
    action, _, raw_shift_id = (callback.data or "").partition(":")
    if action != "delete_last" or user_id is None:
        await callback.message.answer("Не удалось проверить запрос.")
        return
    if raw_shift_id == "cancel":
        await callback.message.answer(
            "Смена оставлена без изменений.",
            reply_markup=main_menu_keyboard(user_id),
        )
        return

    _, _, shift_id_text = raw_shift_id.partition(":")
    try:
        shift_id = int(shift_id_text)
    except ValueError:
        await callback.message.answer(
            "Не удалось определить смену. Данные не удалены.",
            reply_markup=main_menu_keyboard(user_id),
        )
        return

    try:
        deleted = delete_shift_for_user(user_id, shift_id)
    except sqlite3.Error:
        logging.exception("Failed to delete a user's shift")
        await callback.message.answer(
            "⚠️ Не удалось удалить смену. Данные не изменены.",
            reply_markup=main_menu_keyboard(user_id),
        )
        return

    result = (
        "✅ Последняя смена удалена."
        if deleted
        else "Эта смена уже удалена или недоступна."
    )
    await callback.message.answer(
        result,
        reply_markup=main_menu_keyboard(user_id),
    )


@router.message(StateFilter(None), F.text == ADMIN_PANEL_BUTTON)
async def show_admin_panel(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else None
    if not is_admin(user_id):
        await message.answer(
            "⛔ Доступ к панели мастера закрыт.",
            reply_markup=menu_keyboard_for(message),
        )
        return

    try:
        chunks = admin_report_chunks(all_saved_shifts())
    except sqlite3.Error:
        logging.exception("Failed to load admin shift summary")
        await message.answer(
            "⚠️ Не удалось загрузить сводку. Попробуйте позже.",
            reply_markup=menu_keyboard_for(message),
        )
        return

    for index, chunk in enumerate(chunks):
        await message.answer(
            chunk,
            reply_markup=admin_panel_keyboard(user_id)
            if index == len(chunks) - 1
            else None,
        )


@router.message(StateFilter(None), F.text == CLEAR_TEST_DATA_BUTTON)
async def request_clear_all_shifts(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else None
    if not is_admin(user_id):
        await message.answer(
            "⛔ Доступ к очистке тестовых данных закрыт.",
            reply_markup=menu_keyboard_for(message),
        )
        return

    try:
        shift_count = count_saved_shifts()
    except sqlite3.Error:
        logging.exception("Failed to count shifts before admin cleanup")
        await message.answer(
            "⚠️ Не удалось проверить количество смен. Данные не изменены.",
            reply_markup=admin_panel_keyboard(user_id),
        )
        return
    if shift_count == 0:
        await message.answer(
            "В базе нет смен для очистки.",
            reply_markup=admin_panel_keyboard(user_id),
        )
        return

    await message.answer(
        "⚠️ <b>Очистить все сохранённые смены?</b>\n"
        f"Будет удалено смен: <b>{shift_count}</b>.\n"
        "Это затронет всех работников и удалит все линии и начисления. "
        "Действие необратимо.",
        reply_markup=clear_all_confirmation_keyboard(),
    )


@router.callback_query(F.data.startswith("admin_clear:"))
async def confirm_clear_all_shifts(callback: CallbackQuery) -> None:
    await callback.answer()
    if not isinstance(callback.message, Message):
        return
    await callback.message.edit_reply_markup(reply_markup=None)

    user_id = callback.from_user.id if callback.from_user else None
    if not is_admin(user_id):
        await callback.message.answer("⛔ Доступ закрыт.")
        return

    action, _, decision = (callback.data or "").partition(":")
    if action != "admin_clear":
        await callback.message.answer("Не удалось проверить запрос.")
        return
    if decision == "cancel":
        await callback.message.answer(
            "Данные оставлены без изменений.",
            reply_markup=admin_panel_keyboard(user_id),
        )
        return
    if decision != "confirm":
        await callback.message.answer(
            "Неизвестное действие. Данные не изменены.",
            reply_markup=admin_panel_keyboard(user_id),
        )
        return

    try:
        deleted_count = delete_all_saved_shifts()
    except sqlite3.Error:
        logging.exception("Failed to clear all shifts from admin panel")
        await callback.message.answer(
            "⚠️ Не удалось очистить данные. Попробуйте позже.",
            reply_markup=admin_panel_keyboard(user_id),
        )
        return

    await callback.message.answer(
        f"✅ Очистка завершена. Удалено смен: <b>{deleted_count}</b>.",
        reply_markup=admin_panel_keyboard(user_id),
    )


@router.message(StateFilter(None))
async def show_menu_for_unknown_message(message: Message) -> None:
    await message.answer(
        "Выберите действие кнопкой ниже или отправьте /help.",
        reply_markup=menu_keyboard_for(message),
    )


async def main() -> None:
    load_dotenv()
    settings = Settings.from_environment()
    initialize_database()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher()
    dispatcher.include_router(router)

    logging.getLogger(__name__).info("Starting Telegram shift-income bot")
    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("Bot stopped")
