"""Mini app backend: sahifalarni beradi, audio qabul qiladi, transkripsiya va baholashni boshqaradi.

/          - Konuşma (speaking) imtihoni: savollar, ovoz yozish, baholash
/writing   - Yazma (writing) imtihon rejimi: taymer, so'z hisoblagich, turkcha harflar, baholash"""
import asyncio
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
from access import REDO_MSG, already_done, check_access, done_parts, get_plan, release_check, reserve
import config
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
    if not db.exam_open_for(exam, user["id"]) or (exam.get("kind") or "speaking") != "speaking":
        return _error(404, "Bu imtihon hozir yopiq.")
    questions = await db.get_questions(exam_id)
    if not questions:
        return _error(404, "Bu imtihonda hali savol yo'q.")

    await db.save_user(user["id"], _full_name(user), user.get("username"))
    allowed, reason = await check_access(user["id"], "speaking", exam=exam)
    if not allowed:
        return _error(403, reason)
    if await already_done(user["id"], exam):
        return _error(403, REDO_MSG)
    # mini app qayta ochilsa (yoki «Qayta urinish») - yangi bo'sh urinish yaratilmaydi, oxirgisi davom etadi
    attempt_id = await db.reusable_attempt(user["id"], exam_id) or await db.create_attempt(user["id"], exam_id)
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
            return _error(503, "Ovozni matnga aylantirish xizmati hozir band.")

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
        if not any((a.get("transcript") or "").strip() for a in answers):
            # ovoz umuman eshitilmadi (mikrofon o'chiq / noto'g'ri qurilma) - limit sarflanmaydi
            return _error(400, "Ovozingiz eshitilmadi — mikrofon ishlamayotgan bo'lishi mumkin. Mikrofonni tekshirib, "
                               "imtihonni qaytadan boshlang. Bu urinish limitga kirmadi.")
        exam = await db.get_exam(attempt["exam_id"])
        if await already_done(user["id"], exam):  # shu qism boshqa oynada allaqachon topshirilgan
            return _error(403, REDO_MSG)
        # Imtihon davomida limit tugagan bo'lishi mumkin; shu davrda ochilgan mock qismi - limitsiz
        allowed, reason, held = await reserve(user["id"], "speaking", exam)
        if not allowed:
            return _error(403, reason)
        try:
            part = exam.get("part") if exam else None
            result = await grader.grade_speaking(answers, part, plans.raters_for(await get_plan(user["id"])))
            await db.finish_attempt(attempt_id, result["total"], result["level"], result, result["raw"])
        finally:
            if held:
                release_check(user["id"], "speaking")
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


# ---------- Full mock (Konuşma): Part 1.1 -> 1.2 -> 2 -> 3 ketma-ket ----------

PART_ORDER = {p: i for i, p in enumerate(db.MOCK_SPEAKING_PARTS)}


async def _mock_parts(mock_id: int) -> list[dict]:
    """Mockning savoli bor Konuşma qismlari, rasmiy tartibda."""
    exams = [ex for ex in await db.mock_exams(mock_id)
             if (ex.get("kind") or "speaking") == "speaking" and ex.get("q_count") and ex.get("is_active")]
    return sorted(exams, key=lambda ex: PART_ORDER.get(ex.get("part"), 99))


async def start_mock(request: web.Request):
    user = _user_or_401(request)
    mock = await db.get_mock(int(request.match_info["mock_id"]))
    parts = await _mock_parts(mock["id"]) if mock else []
    if not parts:
        return _error(404, "Bu mock hozir yopiq.")
    await db.save_user(user["id"], _full_name(user), user.get("username"))
    allowed, reason = await check_access(user["id"], "speaking", exam=parts[0])
    if not allowed:
        return _error(403, reason)
    if (await done_parts(user["id"], mock["id"]))["speaking"]:
        return _error(403, "Bu mockning Konuşma qismlaridan birini allaqachon ishlagansiz — Full mock yopiq. "
                           "Qolgan qismlarni alohida ishlang yoki keyingi mockni tanlang 📚")
    items, attempts = [], []
    for ex in parts:
        attempt_id = await db.reusable_attempt(user["id"], ex["id"]) or await db.create_attempt(user["id"], ex["id"])
        attempts.append(attempt_id)
        info = part_info("speaking", ex.get("part"))
        photo = None
        for q in await db.get_questions(ex["id"]):
            photo = _photo_url(q) or (photo if info.get("carry_photo") else None)
            items.append({"id": q["id"], "text": q["text"], "photo": photo, "prep_sec": q["prep_sec"],
                          "answer_sec": q["answer_sec"], "attempt_id": attempt_id,
                          "part": ex.get("part"), "part_name": info["name"].split(" — ")[0]})
    return web.json_response({
        "mock_id": mock["id"],
        "attempt_ids": attempts,
        "title": mock["title"],
        "part_name": "Full mock — " + " → ".join(f"Part {ex.get('part')}" for ex in parts),
        "flag": LANG["flag"],
        "section_name": LANG["speaking_name"],
        "answer_rule": LANG["answer_rule"],
        "questions": items,
    })


async def finish_mock(request: web.Request):
    """Full mock yakuni: har qism o'z mezoni bilan baholanadi (baholangani qayta baholanmaydi), natija
    bitta umumiy natijaga yig'iladi. Limit - mock uchun bir marta."""
    user = _user_or_401(request)
    try:
        body = await request.json()
        mock_id = int(body.get("mock_id", 0))
        attempt_ids = [int(a) for a in body.get("attempt_ids") or []][:8]
    except (ValueError, TypeError, AttributeError):
        return _error(400, "So'rov noto'g'ri.")
    mock = await db.get_mock(mock_id)
    if not mock or not attempt_ids:
        return _error(404, "Mock topilmadi.")
    rows = []
    for aid in attempt_ids:
        attempt = await _own_attempt(user, aid)
        exam = await db.get_exam(attempt["exam_id"]) if attempt else None
        if not attempt or not exam or exam.get("mock_id") != mock_id:
            return _error(403, "Urinish topilmadi.")
        rows.append((attempt, exam))
    rows.sort(key=lambda r: PART_ORDER.get(r[1].get("part"), 99))
    if any(a["id"] in _grading for a, _ in rows):
        return _error(409, "Javoblaringiz hozir baholanmoqda — bir oz kutib, «Qayta urinish»ni bosing.")

    bot: Bot = request.app["bot"]
    ids = [a["id"] for a, _ in rows]
    _grading.update(ids)
    held = False
    try:
        todo, results = [], {}
        for attempt, exam in rows:
            attempt = await db.get_attempt(attempt["id"])
            if attempt["status"] == "done":
                results[attempt["id"]] = json.loads(attempt["result_json"])
                continue
            answers = await db.get_answers(attempt["id"])
            if answers:
                todo.append((attempt, exam, answers))
        heard = any((a.get("transcript") or "").strip() for _, _, ans in todo for a in ans)
        if todo and not heard and not results:
            return _error(400, "Ovozingiz eshitilmadi — mikrofon ishlamayotgan bo'lishi mumkin. Mikrofonni tekshirib, "
                               "mockni qaytadan boshlang. Bu urinish limitga kirmadi.")
        for _, exam, _ in todo:
            if await already_done(user["id"], exam):
                return _error(403, REDO_MSG)
        if todo:
            allowed, reason, held = await reserve(user["id"], "speaking", rows[0][1])
            if not allowed:
                return _error(403, reason)
            raters = plans.raters_for(await get_plan(user["id"]))

            async def grade(attempt, exam, answers):
                result = await grader.grade_speaking(answers, exam.get("part"), raters)
                await db.finish_attempt(attempt["id"], result["total"], result["level"], result, result["raw"])
                results[attempt["id"]] = result

            outcome = await asyncio.gather(*[grade(*t) for t in todo], return_exceptions=True)
            errors = [o for o in outcome if isinstance(o, BaseException)]
            if errors:
                raise errors[0]
        if not results:
            return _error(400, "Hech bir javob yozib olinmadi.")
        combined = grader.combine_mock([results[a["id"]] for a, _ in rows if a["id"] in results])
    except Exception as e:
        log.exception("Full mock baholash xatosi (mock %s)", mock_id)
        if getattr(e, "status", None) == 429:
            return _error(503, "Hozir baholash navbati band. Javoblaringiz saqlandi - "
                               "1-2 daqiqadan keyin «Qayta urinish»ni bosing.")
        await grader.report_key_problem(bot, e)
        return _error(503, "Baholashda xatolik bo'ldi. Javoblaringiz saqlandi — bir daqiqadan keyin «Qayta urinish»ni bosing.")
    finally:
        _grading.difference_update(ids)
        if held:
            release_check(user["id"], "speaking")

    if todo:  # birinchi marta yakunlandi - xabar yuboriladi (qayta so'rovda takrorlanmaydi)
        try:
            await grader.send_long(bot, user["id"], grader.result_message(mock["title"], combined["part_name"], combined))
        except Exception:
            log.exception("Full mock natijasini o'quvchiga yuborib bo'lmadi")
        name = html.escape(user.get("first_name", "?"))
        await grader.notify_admins(
            bot,
            f"📥 🎙 {name} (<code>{user['id']}</code>) — {html.escape(mock['title'])} (Full mock): "
            f"<b>{combined['total']}/75</b> ({html.escape(combined['level'])})",
        )
    return web.json_response(combined)


# ---------- Yazma (writing) - imtihon rejimi ----------

async def _writing_exam(exam_id: int, user_id: int, resume: bool = False,
                        mode: str | None = None) -> tuple[dict | None, dict | None, dict]:
    """(mavzu, topshiriq qatori, maydonlar) - mavzu ochiq va topshirig'i to'liq bo'lsagina.
    resume=True: o'quvchi saqlangan qoralamasini davom ettiryapti - mavzu yopilgan bo'lsa ham ochiladi."""
    exam = await db.get_exam(exam_id)
    if not exam or exam.get("kind") != "writing":
        return None, None, {}
    if exam.get("owner_id") is not None and exam["owner_id"] != user_id:
        return None, None, {}  # boshqa o'quvchining shaxsiy testi
    if not resume and not db.exam_open_for(exam, user_id):
        return None, None, {}
    row = await db.get_writing_task(exam_id)
    part = writing_mode(exam, mode)
    values = writing.task_values(exam.get("part"), row)
    if not row or writing.missing_fields(part, values):
        return None, None, {}
    return exam, row, values


def writing_mode(exam: dict, mode) -> str | None:
    """Mockdagi to'liq yazmadan faqat bitta qismni ishlash: mode "1" / "2" (aks holda - testning o'z turi)."""
    mode = str(mode or "")
    return mode if exam.get("part") == "full" and mode in ("1", "2") else exam.get("part")


async def start_writing(request: web.Request):
    user = _user_or_401(request)
    exam, row, values = await _writing_exam(int(request.match_info["exam_id"]), user["id"],
                                            request.query.get("resume") == "1", request.query.get("mode"))
    if not exam:
        return _error(404, "Bu mavzu hozir yopiq.")
    await db.save_user(user["id"], _full_name(user), user.get("username"))
    allowed, reason = await check_access(user["id"], "writing", exam=exam)
    if not allowed:
        return _error(403, reason)
    part = writing_mode(exam, request.query.get("mode"))
    if await already_done(user["id"], exam, part):
        return _error(403, REDO_MSG)
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
    if not exam or exam.get("kind") != "writing" or exam.get("owner_id") not in (None, user["id"]):
        return _error(404, "Mavzu topilmadi.")
    exam = {**exam, "part": writing_mode(exam, body.get("mode"))}
    bot: Bot = request.app["bot"]
    texts = {str(k): str(v or "") for k, v in texts.items()}
    try:
        result, _ = await writing.grade_and_store(bot, user["id"], _full_name(user) or "?", exam, texts, elapsed)
    except writing.WritingDenied as denied:
        return _error(403, str(denied))
    except Exception as e:
        log.exception("Yazma topshirish xatosi (exam %s, user %s)", exam_id, user["id"])
        if getattr(e, "status", None) == 429:
            return _error(503, "Hozir tekshirish navbati band. Matnlaringiz saqlangan — 1–2 daqiqadan keyin "
                               "«Qayta urinish»ni bosing.")
        return _error(503, "Tekshirishda xatolik bo'ldi. Matnlaringiz saqlangan — birozdan keyin «Qayta urinish»ni bosing.")
    return web.json_response(result)


# ---------- Tashqi tizim uchun: foydalanuvchilar ro'yxati ----------

_API_PLANS = ("free", "standard", "pro", "vip")


def _iso(stamp: str | None) -> str | None:
    """Bazadagi UTC vaqt ("YYYY-MM-DD HH:MM:SS") -> ISO 8601 ("...T...Z")."""
    return stamp.replace(" ", "T") + "Z" if stamp else None


async def api_users(request: web.Request):
    """GET /api/users - ro'yxatdan o'tgan foydalanuvchilar (faqat USERS_API_KEY bilan).
    Parametrlar: plan=free|standard|pro, since=YYYY-MM-DD (shu sanadan keyin qo'shilganlar),
    limit (standart 500, ko'pi bilan 1000), offset."""
    key = config.USERS_API_KEY
    if not key:
        raise web.HTTPNotFound()  # kalit qo'yilmagan - endpoint umuman yo'qdek
    auth = request.headers.get("Authorization", "")
    token = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
    if not token or not hmac.compare_digest(token.encode(), key.encode()):
        log.warning("/api/users: noto'g'ri kalit (%s)", request.remote)
        return _error(401, "Kalit noto'g'ri yoki berilmagan.")
    q = request.query
    plan = q.get("plan") or None
    if plan and plan not in _API_PLANS:
        return _error(400, "plan faqat free, standard, pro yoki vip bo'lishi mumkin.")
    since = q.get("since") or None
    if since:
        try:
            since = time.strftime("%Y-%m-%d %H:%M:%S", time.strptime(since, "%Y-%m-%d"))
        except ValueError:
            return _error(400, "since formati: YYYY-MM-DD.")
    try:
        limit = min(max(int(q.get("limit", 500)), 1), 1000)
        offset = max(int(q.get("offset", 0)), 0)
    except ValueError:
        return _error(400, "limit va offset butun son bo'lishi kerak.")
    rows = await db.users_for_api(plan, since, limit, offset)
    users = [
        {
            "telegram_id": r["telegram_id"],
            "name": r["full_name"],
            "username": r["username"],
            "registered_at": _iso(r["joined_at"]),
            "plan": r["plan"],
            "plan_until": _iso(r["plan_until"]),
            "speaking_count": r["speaking_count"],
            "writing_count": r["writing_count"],
            "last_activity": _iso(r["last_activity"]),
            "blocked": bool(r["blocked"]),
        }
        for r in rows
    ]
    body = {"total": rows[0]["total"] if rows else 0, "limit": limit, "offset": offset, "users": users}
    return web.json_response(body, headers={"Cache-Control": "no-store"},
                             dumps=lambda o: json.dumps(o, ensure_ascii=False))


def create_app(bot: Bot) -> web.Application:
    app = web.Application(client_max_size=MAX_AUDIO_BYTES + 1024 * 1024)
    app["bot"] = bot
    app.router.add_get("/", _page("index.html"))
    app.router.add_get("/writing", _page("writing.html"))
    app.router.add_get("/health", lambda r: web.Response(text="ok"))
    app.router.add_get("/api/users", api_users)
    app.router.add_get(r"/api/exam/{exam_id:\d+}", start_exam)
    app.router.add_get(r"/api/photo/{question_id:\d+}", question_photo)
    app.router.add_post("/api/answer", upload_answer)
    app.router.add_post("/api/finish", finish_exam)
    app.router.add_get(r"/api/mock/{mock_id:\d+}", start_mock)
    app.router.add_post("/api/mock_finish", finish_mock)
    app.router.add_get(r"/api/wexam/{exam_id:\d+}", start_writing)
    app.router.add_post("/api/wsubmit", submit_writing)
    return app
