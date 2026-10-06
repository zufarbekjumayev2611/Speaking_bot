"""Mini app backend: sahifalarni beradi, audio qabul qiladi, transkripsiya va baholashni boshqaradi.

/          - Konuşma (speaking) imtihoni: savollar, ovoz yozish, baholash
/writing   - Yazma (writing) imtihon rejimi: taymer, so'z hisoblagich, turkcha harflar, baholash"""
import hashlib
import hmac
import json
import logging
import os
import time
from collections import OrderedDict
from urllib.parse import parse_qsl

from aiogram import Bot
from aiohttp import web

import db
import grader
import stt
import html

import plans
import writing
from access import check_access, get_plan
from config import BOT_TOKEN, EXAM_LANGUAGE
from languages import LANGUAGES
from parts import part_info

WEBAPP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webapp")
MAX_AUDIO_BYTES = 15 * 1024 * 1024
INIT_DATA_MAX_AGE = 24 * 3600
PHOTO_CACHE_SIZE = 200

LANG = LANGUAGES[EXAM_LANGUAGE]
log = logging.getLogger("web")


# ---------- Telegram foydalanuvchisini tekshirish ----------

def verify_init_data(init_data: str) -> dict | None:
    """Mini app'dan kelgan so'rov haqiqatan Telegram'dan va shu foydalanuvchidan
    ekanini tekshiradi. Soxta so'rovlar (boshqa odam nomidan) rad etiladi."""
    if not init_data:
        return None
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
        received_hash = pairs.pop("hash")
        check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
        secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        computed = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(computed, received_hash):
            return None
        if time.time() - int(pairs.get("auth_date", "0")) > INIT_DATA_MAX_AGE:
            return None
        return json.loads(pairs["user"])
    except Exception:
        return None


def _user_or_401(request: web.Request) -> dict:
    user = verify_init_data(request.headers.get("X-Init-Data", ""))
    if not user:
        raise web.HTTPUnauthorized(text=json.dumps({"error": "Botni Telegram ichidan qayta oching."}), content_type="application/json")
    return user


def _full_name(user: dict) -> str:
    return " ".join(filter(None, [user.get("first_name"), user.get("last_name")]))


def _error(status: int, message: str):
    return web.json_response({"error": message}, status=status)


# ---------- Sahifalar ----------

def _page(name: str):
    async def handler(request: web.Request):
        return web.FileResponse(os.path.join(WEBAPP_DIR, name), headers={"Cache-Control": "no-store"})
    return handler


# ---------- Rasm (Telegram'dan olinib, xotirada saqlanadi) ----------

_photo_cache: "OrderedDict[str, bytes]" = OrderedDict()  # file_id -> rasm (rasm almashtirilsa - yangi file_id)


async def question_photo(request: web.Request):
    q = await db.get_question(int(request.match_info["question_id"]))
    if not q or not q["photo_file_id"]:
        raise web.HTTPNotFound()
    file_id = q["photo_file_id"]
    if file_id not in _photo_cache:
        bot: Bot = request.app["bot"]
        file = await bot.get_file(file_id)
        data = await bot.download_file(file.file_path)
        _photo_cache[file_id] = data.read()
        while len(_photo_cache) > PHOTO_CACHE_SIZE:
            _photo_cache.popitem(last=False)
    return web.Response(body=_photo_cache[file_id], content_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})


def _photo_url(q: dict) -> str | None:
    # file_id dan qisqa belgi: rasm almashtirilsa brauzer eski rasmni keshdan ko'rsatmaydi
    return f"/api/photo/{q['id']}?v={hashlib.sha1(q['photo_file_id'].encode()).hexdigest()[:8]}" if q.get("photo_file_id") else None


# ---------- Konuşma (speaking) ----------

async def start_exam(request: web.Request):
    user = _user_or_401(request)
    exam_id = int(request.match_info["exam_id"])
    exam = await db.get_exam(exam_id)
    if not exam or not exam["is_active"] or (exam.get("kind") or "speaking") != "speaking":
        return _error(404, "Bu imtihon hozir yopiq.")
    questions = await db.get_questions(exam_id)
    if not questions:
        return _error(404, "Bu imtihonda hali savol yo'q.")

    await db.save_user(user["id"], _full_name(user), user.get("username"))
    allowed, reason = await check_access(user["id"])
    if not allowed:
        return _error(403, reason)
    attempt_id = await db.create_attempt(user["id"], exam_id)
    info = part_info("speaking", exam.get("part"))
    items, photo = [], None
    for q in questions:
        # Part 1.2: rasmlar 4-6-savollar davomida ko'rinib turadi (keyingi savolda rasm bo'lmasa - avvalgisi)
        photo = _photo_url(q) or (photo if info.get("carry_photo") else None)
        items.append({"id": q["id"], "text": q["text"], "photo": photo, "prep_sec": q["prep_sec"], "answer_sec": q["answer_sec"]})
    return web.json_response(
        {
            "attempt_id": attempt_id,
            "title": exam["title"],
            "part_name": info["name"],
            "flag": LANG["flag"],
            "section_name": LANG["speaking_name"],
            "answer_rule": LANG["answer_rule"],
            "questions": items,
        }
    )


async def _own_attempt(user: dict, attempt_id: int):
    attempt = await db.get_attempt(attempt_id)
    if not attempt or attempt["telegram_id"] != user["id"]:
        return None
    return attempt


_grading: set[int] = set()  # hozir baholanayotgan urinishlar - bitta urinish ikki marta baholanmasin


async def upload_answer(request: web.Request):
    user = _user_or_401(request)
    form = await request.post()
    try:
        attempt_id = int(form["attempt_id"])
        question_id = int(form["question_id"])
        audio_field = form["audio"]
        duration = max(0.0, min(float(form.get("duration") or 0), 600.0))
    except (KeyError, ValueError, TypeError):
        return _error(400, "So'rov noto'g'ri.")

    attempt = await _own_attempt(user, attempt_id)
    if not attempt or attempt["status"] != "in_progress" or attempt_id in _grading:
        return _error(403, "Bu urinish yakunlangan yoki sizga tegishli emas.")
    question = await db.get_question(question_id)
    if not question or question["exam_id"] != attempt["exam_id"]:
        return _error(400, "Savol bu imtihonga tegishli emas.")

    audio = audio_field.file.read()
    if len(audio) > MAX_AUDIO_BYTES:
        return _error(413, "Yozuv juda katta.")

    transcript = ""
    if len(audio) > 1000:  # juda kichik fayl = deyarli bo'sh yozuv
        try:
            transcript = await stt.transcribe(audio, audio_field.content_type)
        except Exception:
            log.exception("Transkripsiya xatosi (attempt %s)", attempt_id)
            return _error(503, "Ovozni matnga aylantirib bo'lmadi. Qayta yozib ko'ring.")

    await db.save_answer(attempt_id, question_id, transcript, duration)
    return web.json_response({"ok": True, "heard": bool(transcript)})


async def finish_exam(request: web.Request):
    user = _user_or_401(request)
    try:
        body = await request.json()
        attempt_id = int(body.get("attempt_id", 0))
    except (ValueError, TypeError, AttributeError):
        return _error(400, "So'rov noto'g'ri.")
    attempt = await _own_attempt(user, attempt_id)
    if not attempt:
        return _error(403, "Urinish topilmadi.")
    if attempt["status"] == "done":
        return web.json_response(json.loads(attempt["result_json"]))
    if attempt_id in _grading:
        return _error(409, "Javoblaringiz hozir baholanmoqda — bir oz kutib, «Qayta urinish»ni bosing.")

    bot: Bot = request.app["bot"]
    _grading.add(attempt_id)  # tekshiruv va belgilash orasida await yo'q - ikkinchi so'rov 409 oladi
    try:
        attempt = await db.get_attempt(attempt_id)  # kutish paytida boshqa so'rov baholab bo'lgan bo'lishi mumkin
        if attempt["status"] == "done":
            return web.json_response(json.loads(attempt["result_json"]))
        answers = await db.get_answers(attempt_id)
        if not answers:
            return _error(400, "Hech bir javob yozib olinmadi.")
        # Imtihon davomida limit tugagan bo'lishi mumkin (masalan, bir vaqtda bir nechta imtihon ochilgan)
        allowed, reason = await check_access(user["id"])
        if not allowed:
            return _error(403, reason)
        exam = await db.get_exam(attempt["exam_id"])
        part = exam.get("part") if exam else None
        result = await grader.grade_speaking(answers, part, plans.raters_for(await get_plan(user["id"])))
        await db.finish_attempt(attempt_id, result["total"], result["level"], result, result["raw"])
    except Exception as e:
        log.exception("Baholash xatosi (attempt %s)", attempt_id)
        if getattr(e, "status", None) == 429:
            # Groq bepul rejasi limiti (daqiqalik yoki kunlik) - biroz kutish kerak.
            return _error(503, "Hozir baholash navbati band. Javoblaringiz saqlandi - "
                               "1-2 daqiqadan keyin «Qayta urinish»ni bosing.")
        await grader.report_key_problem(bot, e)
        return _error(503, "Baholashda xatolik bo'ldi. Bir daqiqadan keyin «Qayta urinish»ni bosing.")
    finally:
        _grading.discard(attempt_id)

    title = exam["title"] if exam else "Imtihon"
    try:
        await grader.send_long(bot, user["id"], grader.result_message(title, result["part_name"], result))
    except Exception:
        log.exception("Natijani o'quvchiga yuborib bo'lmadi")
    name = html.escape(user.get("first_name", "?"))
    await grader.notify_admins(
        bot,
        f"📥 🎙 {name} (<code>{user['id']}</code>) — {html.escape(title)}: "
        f"<b>{float(result['raw']):g}/{result['max_raw']}</b> ({html.escape(result['level'])})",
    )
    return web.json_response(result)


# ---------- Yazma (writing) - imtihon rejimi ----------

async def _writing_exam(exam_id: int) -> tuple[dict | None, dict | None, dict]:
    """(mavzu, topshiriq qatori, maydonlar) - mavzu ochiq va topshirig'i to'liq bo'lsagina."""
    exam = await db.get_exam(exam_id)
    if not exam or not exam["is_active"] or exam.get("kind") != "writing":
        return None, None, {}
    row = await db.get_writing_task(exam_id)
    values = writing.task_values(exam.get("part"), row)
    if not row or writing.missing_fields(exam.get("part"), values):
        return None, None, {}
    return exam, row, values


async def start_writing(request: web.Request):
    user = _user_or_401(request)
    exam, row, values = await _writing_exam(int(request.match_info["exam_id"]))
    if not exam:
        return _error(404, "Bu mavzu hozir yopiq.")
    await db.save_user(user["id"], _full_name(user), user.get("username"))
    allowed, reason = await check_access(user["id"])
    if not allowed:
        return _error(403, reason)
    part = exam.get("part")
    info = part_info("writing", part)
    return web.json_response(
        {
            "exam_id": exam["id"],
            "title": exam["title"],
            "part_name": info["name"],
            "time_min": info.get("time") or 60,
            "flag": LANG["flag"],
            "section_name": LANG["writing_name"],
            "lang_name": LANG["name_uz"],
            "special_chars": LANG.get("special_chars", ""),
            "photo": _photo_url(row),
            "components": writing.component_payload(part, values),
        }
    )


async def submit_writing(request: web.Request):
    user = _user_or_401(request)
    try:
        body = await request.json()
        exam_id = int(body.get("exam_id", 0))
        texts = body.get("texts") or {}
        elapsed = max(0, min(int(float(body.get("elapsed_sec") or 0)), 24 * 3600))
        if not isinstance(texts, dict):
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        return _error(400, "So'rov noto'g'ri.")

    exam = await db.get_exam(exam_id)
    if not exam or exam.get("kind") != "writing":
        return _error(404, "Mavzu topilmadi.")
    bot: Bot = request.app["bot"]
    texts = {str(k): str(v or "") for k, v in texts.items()}
    try:
        result, _ = await writing.grade_and_store(bot, user["id"], _full_name(user) or "?", exam, texts, elapsed)
    except writing.WritingDenied as denied:
        return _error(403, str(denied))
    except Exception as e:
        if getattr(e, "status", None) == 429:
            return _error(503, "Hozir tekshirish navbati band. Matnlaringiz saqlangan — 1–2 daqiqadan keyin "
                               "«Qayta urinish»ni bosing.")
        return _error(503, "Tekshirishda xatolik bo'ldi. Matnlaringiz saqlangan — birozdan keyin «Qayta urinish»ni bosing.")
    return web.json_response(result)


def create_app(bot: Bot) -> web.Application:
    app = web.Application(client_max_size=MAX_AUDIO_BYTES + 1024 * 1024)
    app["bot"] = bot
    app.router.add_get("/", _page("index.html"))
    app.router.add_get("/writing", _page("writing.html"))
    app.router.add_get("/health", lambda r: web.Response(text="ok"))
    app.router.add_get(r"/api/exam/{exam_id:\d+}", start_exam)
    app.router.add_get(r"/api/photo/{question_id:\d+}", question_photo)
    app.router.add_post("/api/answer", upload_answer)
    app.router.add_post("/api/finish", finish_exam)
    app.router.add_get(r"/api/wexam/{exam_id:\d+}", start_writing)
    app.router.add_post("/api/wsubmit", submit_writing)
    return app
