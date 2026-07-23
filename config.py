import os
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BOT_TOKEN = "сюда токен вставить от бота"
GEMINI_KEY = "сюда вставлять API ключик полученный через Google AI Studio"
RAW_VIDEO_DIR = r"D:/Recordings/Raw" 
CLIPS_OUTPUT_DIR = r"D:/Recordings/FinishedClips"
os.makedirs(RAW_VIDEO_DIR, exist_ok=True)
os.makedirs(CLIPS_OUTPUT_DIR, exist_ok=True)
PREVIEW_VIDEO = os.path.join(BASE_DIR, "preview_temp.mp4")