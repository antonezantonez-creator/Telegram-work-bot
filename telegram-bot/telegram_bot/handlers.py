from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

router = Router(name="main")


@router.message(CommandStart())
async def handle_start(message: Message) -> None:
    await message.answer(
        "Hi! I’m your new Telegram bot.\n\n"
        "Send /help to see what I can do, or send me a text message."
    )


@router.message(Command("help"))
async def handle_help(message: Message) -> None:
    await message.answer(
        "Available commands:\n"
        "/start — start the bot\n"
        "/help — show this help message\n\n"
        "For now, I’ll repeat any text message you send."
    )


@router.message()
async def echo_text(message: Message) -> None:
    if message.text:
        await message.answer(message.text)
