import os
import logging
import asyncio
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from config import BOT_TOKEN, RAW_VIDEO_DIR
from core import process_local_pipeline

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


class MontageStates(StatesGroup):
    waiting_for_file_path = State()
    waiting_for_custom_prompt = State()


def get_presets_keyboard():
    buttons = [
        [InlineKeyboardButton(text="Динамика и фраги", callback_data="preset_kills")],
        [InlineKeyboardButton(text="Нестандартные моменты", callback_data="preset_fails")],
        [InlineKeyboardButton(text="Полная подборка", callback_data="preset_full")],
        [InlineKeyboardButton(text="Пользовательский запрос", callback_data="preset_custom")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    await state.set_state(MontageStates.waiting_for_file_path)
    await message.answer(
        "Панель обработки видеозаписей.\n\n"
        "Укажите имя файла или абсолютный путь к видеозаписи:\n"
        "Пример: `game.mp4` или `D:\\Recordings\\Raw\\match.mp4`",
        parse_mode="Markdown"
    )


@dp.message(MontageStates.waiting_for_file_path, F.text & ~F.text.startswith("/"))
async def handle_file_input(message: types.Message, state: FSMContext):
    file_ref = message.text.strip()
    full_path = file_ref if os.path.isabs(file_ref) else os.path.join(RAW_VIDEO_DIR, file_ref)

    if not os.path.exists(full_path):
        await message.answer(
            f"Файл не найден:\n`{full_path}`\n\nПроверьте путь и отправьте значение повторно.", 
            parse_mode="Markdown"
        )
        return

    await state.update_data(video_path=full_path)
    
    await message.answer(
        f"Файл подтвержден: `{os.path.basename(full_path)}`\n\n"
        f"Выберите пресет для поиска моментов:",
        parse_mode="Markdown",
        reply_markup=get_presets_keyboard()
    )


@dp.callback_query(F.data.startswith("preset_"))
async def process_preset_click(callback: types.CallbackQuery, state: FSMContext):
    preset = callback.data.split("_")[1]
    data = await state.get_data()
    video_path = data.get("video_path")

    if not video_path:
        await callback.message.edit_text("Ошибка сессии. Сбросьте состояние командой /start")
        return

    if preset == "custom":
        await state.set_state(MontageStates.waiting_for_custom_prompt)
        await state.update_data(video_path=video_path)
        
        await callback.message.edit_text(
            "Введите описание целевых событий для поиска:\n"
            "Пример: *уничтожение техники, попадания из пушки, сообщения в логе*",
            parse_mode="Markdown"
        )
        return

    status_msg = await callback.message.edit_text("Подготовка превью-файла...", parse_mode="Markdown")
    await run_bot_pipeline(status_msg, video_path, preset_type=preset)


@dp.message(MontageStates.waiting_for_custom_prompt, F.text & ~F.text.startswith("/"))
async def handle_custom_prompt_input(message: types.Message, state: FSMContext):
    custom_text = message.text.strip()
    data = await state.get_data()
    video_path = data.get("video_path")
    
    if not video_path:
        await message.answer("Путь к файлу не найден. Начните заново с отправки файла.")
        await state.set_state(MontageStates.waiting_for_file_path)
        return

    await state.clear()
    
    status_msg = await message.answer("Запрос принят. Обработка видеозаписи...", parse_mode="Markdown")
    await run_bot_pipeline(status_msg, video_path, preset_type="custom", custom_prompt=custom_text)


async def run_bot_pipeline(status_msg: types.Message, video_path: str, preset_type: str, custom_prompt: str = None):
    try:
        await status_msg.edit_text("Анализ видеоряда и поиск фрагментов...", parse_mode="Markdown")
        
        success, message_text, saved_clip_path, clips_list = await asyncio.to_thread(
            process_local_pipeline, video_path, preset_type, custom_prompt
        )

        if not success:
            await status_msg.edit_text(message_text)
            return

        await status_msg.edit_text("Монтаж и сборка итогового файла...", parse_mode="Markdown")

        clips_text = ""
        total_duration = 0
        for idx, clip in enumerate(clips_list, 1):
            dur = clip['end_sec'] - clip['start_sec']
            total_duration += dur
            clips_text += f"{idx}. **[{clip['start_sec']}с — {clip['end_sec']}с]** ({dur}с) — {clip['reason']}\n"

        report = (
            f"**Обработка завершена**\n\n"
            f"Найдено фрагментов: {len(clips_list)}\n"
            f"Длительность ролика: {total_duration} сек.\n\n"
            f"**Список моментов:**\n{clips_text}\n"
            f"**Файл сохранен:**\n`{saved_clip_path}`"
        )

        await status_msg.edit_text(report, parse_mode="Markdown")

    except Exception as e:
        await status_msg.edit_text(f"Ошибка выполнения: `{e}`", parse_mode="Markdown")


async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())