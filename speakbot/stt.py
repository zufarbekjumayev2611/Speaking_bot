"""Nutqni matnga aylantirish: Groq Whisper (bepul rejada kuniga ~8 soat audio)."""
import aiohttp

from config import EXAM_LANGUAGE, GROQ_API_KEY, STT_MODEL
from languages import LANGUAGES
from netclient import get_session

LANG = LANGUAGES[EXAM_LANGUAGE]

GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"

# Brauzer yozuv formatini fayl kengaytmasiga moslash (Groq kengaytmaga qaraydi).
_EXT = {
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/mp4": "m4a",
    "audio/mpeg": "mp3",
    "audio/wav": "wav",
    "audio/x-m4a": "m4a",
}


async def transcribe(audio: bytes, mime: str) -> str:
    ext = _EXT.get((mime or "").split(";")[0].strip(), "webm")
    form = aiohttp.FormData()
    form.add_field("file", audio, filename=f"answer.{ext}", content_type=mime or "audio/webm")
    form.add_field("model", STT_MODEL)
    form.add_field("language", LANG["whisper_code"])
    form.add_field("response_format", "json")
    form.add_field("temperature", "0")
    # Whisper to'xtalishlarni (um, ııı...) o'chirib tashlamasligi uchun namuna - ravonlikni baholashga kerak.
    form.add_field("prompt", LANG["whisper_prompt"])

    async with get_session().post(
        GROQ_URL,
        data=form,
        headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
        timeout=aiohttp.ClientTimeout(total=60),
    ) as resp:
        body = await resp.json(content_type=None)
        if resp.status != 200:
            raise RuntimeError(f"Groq xatosi {resp.status}: {body}")
        return (body.get("text") or "").strip()