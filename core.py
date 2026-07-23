import os
import json
import time
import subprocess
from datetime import datetime
from google import genai
from google.genai import types as genai_types
from google.genai.errors import APIError, ServerError
from pydantic import BaseModel, Field
from config import GEMINI_KEY, PREVIEW_VIDEO, CLIPS_OUTPUT_DIR, BASE_DIR

client = genai.Client(api_key=GEMINI_KEY)

FFMPEG_EXE = os.path.join(BASE_DIR, "ffmpeg.exe")
if not os.path.exists(FFMPEG_EXE):
    FFMPEG_EXE = "ffmpeg"


class Clip(BaseModel):
    start_sec: int = Field(description="Время начала фрагмента в секундах")
    end_sec: int = Field(description="Время окончания фрагмента в секундах")
    reason: str = Field(description="Описание события в кадре")

class VideoAnalysis(BaseModel):
    clips: list[Clip]


def compress_video_cpu(input_path: str, output_path: str):
    cmd = [
        FFMPEG_EXE, "-y",
        "-i", input_path,
        "-vf", "scale=1280:-1,fps=10",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "30",
        "-an",
        output_path
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)


def call_api_with_retry(video_file, selected_prompt, retries=5):
    safety_settings = [
        genai_types.SafetySetting(category=genai_types.HarmCategory.HARM_CATEGORY_HARASSMENT, threshold=genai_types.HarmBlockThreshold.BLOCK_NONE),
        genai_types.SafetySetting(category=genai_types.HarmCategory.HARM_CATEGORY_HATE_SPEECH, threshold=genai_types.HarmBlockThreshold.BLOCK_NONE),
        genai_types.SafetySetting(category=genai_types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, threshold=genai_types.HarmBlockThreshold.BLOCK_NONE),
        genai_types.SafetySetting(category=genai_types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, threshold=genai_types.HarmBlockThreshold.BLOCK_NONE),
    ]

    models = [
        'models/gemini-3-flash-preview',
        'models/gemini-3.5-flash',
        'models/gemini-2.0-flash'
    ]

    for model_name in models:
        for attempt in range(1, retries + 1):
            try:
                return client.models.generate_content(
                    model=model_name,
                    contents=[video_file, selected_prompt],
                    config=genai_types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=VideoAnalysis,
                        temperature=0.2,
                        safety_settings=safety_settings
                    )
                )
            except (ServerError, APIError) as e:
                err_str = str(e)
                if "404" in err_str or "NOT_FOUND" in err_str:
                    break
                if any(code in err_str for code in ["503", "429", "RESOURCE_EXHAUSTED", "UNAVAILABLE"]):
                    time.sleep(4 * attempt)
                else:
                    raise e

    raise Exception("Сервер временно недоступен. Попробуйте повторить запрос позже.")


def analyze_video(preview_path: str, preset: str, custom_prompt: str = None):
    uploaded_file = client.files.upload(file=preview_path)
    
    try:
        while uploaded_file.state.name == "PROCESSING":
            time.sleep(2)
            uploaded_file = client.files.get(name=uploaded_file.name)

        if preset == "custom" and custom_prompt:
            base_prompt = custom_prompt
        else:
            prompts = {
                "kills": "Найди ключевые игровые моменты: устранение противников, уничтожение военной техники, серийные ликвидации.",
                "fails": "Найди ошибки и нестандартные ситуации: случайный самоподрыв, промахи с близкой дистанции, гибель от техники.",
                "full": "Найди все наиболее динамичные и интересные моменты на протяжении всей видеозаписи."
            }
            base_prompt = prompts.get(preset, prompts["full"])

        selected_prompt = (
            f"{base_prompt}\n"
            "Инструкция: выдели таймкоды строго для активного геймплея. "
            "Для каждого события задай интервал: 3 секунды ДО и 2 секунды ПОСЛЕ. "
            "Если на видео отсутствует игровой процесс (рабочий стол, меню, сторонний софт) — не добавляй такие фрагменты. "
            "Если подходящих событий нет, верни пустой список clips."
        )

        response = call_api_with_retry(uploaded_file, selected_prompt)
        result = json.loads(response.text)
        return result.get("clips", [])
        
    finally:
        client.files.delete(name=uploaded_file.name)


def cut_and_merge_clips(input_path: str, clips: list) -> str:
    temp_files = []
    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    list_file_path = os.path.join(CLIPS_OUTPUT_DIR, f"concat_list_{timestamp_str}.txt")

    try:
        for idx, clip in enumerate(clips):
            start_sec = clip['start_sec']
            duration = clip['end_sec'] - start_sec
            temp_clip_path = os.path.join(CLIPS_OUTPUT_DIR, f"temp_{timestamp_str}_{idx}.mp4")
            
            cmd_cut = [
                FFMPEG_EXE, "-y",
                "-ss", str(start_sec),
                "-i", input_path,
                "-t", str(duration),
                "-c", "copy",
                temp_clip_path
            ]
            subprocess.run(cmd_cut, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            temp_files.append(temp_clip_path)

        with open(list_file_path, "w", encoding="utf-8") as f:
            for tf in temp_files:
                safe_path = tf.replace("\\", "/")
                f.write(f"file '{safe_path}'\n")

        final_filename = f"compilation_{timestamp_str}.mp4"
        final_output_path = os.path.join(CLIPS_OUTPUT_DIR, final_filename)

        cmd_concat = [
            FFMPEG_EXE, "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", list_file_path,
            "-c", "copy",
            final_output_path
        ]
        subprocess.run(cmd_concat, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

        return final_output_path

    finally:
        for tf in temp_files:
            if os.path.exists(tf):
                os.remove(tf)
        if os.path.exists(list_file_path):
            os.remove(list_file_path)


def process_local_pipeline(video_path: str, preset_type: str, custom_prompt: str = None):
    if not os.path.exists(video_path):
        return False, "Файл не найден по указанному пути.", None, None

    compress_video_cpu(video_path, PREVIEW_VIDEO)

    try:
        clips = analyze_video(PREVIEW_VIDEO, preset_type, custom_prompt)
        
        if not clips:
            return False, "Подходящие фрагменты не обнаружены.", None, None

        final_clip_path = cut_and_merge_clips(video_path, clips)
        return True, "Обработка завершена успешно", final_clip_path, clips

    finally:
        if os.path.exists(PREVIEW_VIDEO):
            os.remove(PREVIEW_VIDEO)