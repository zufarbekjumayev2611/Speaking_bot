"""Telegram bot: o'quvchi uchun Speaking va Writing bo'limlari, admin uchun panel.

O'quvchi:  Speaking -> tur (Part 1.1 / 1.2 / 2 / 3) -> imtihon -> mini app
           Writing  -> tur (1-qism / 2-qism / to'liq imtihon) -> mavzu -> imtihon rejimi (mini app:
                       taymer, so'z hisoblagich) yoki chatda: har bir matnni xabar qilib yuboradi
Admin:     ➕ Konuşma testi / ➕ Yazma mavzusi -> tur -> nom -> savollar / topshiriq
           (yazma topshirig'i PDF tuzilishi bo'yicha qismlab so'raladi: kelgan xat, ko'rsatmalar,
           2-qism topshirig'i; har bir qismni keyin alohida tahrirlash mumkin)
"""
import asyncio
import html
import json
import logging
import time

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    WebAppInfo,
)

import db
import grader
import report
import writing
from access import check_access
from config import EXAM_LANGUAGE, WEBAPP_URL, is_admin
from languages import LANGUAGES
from parts import part_info, parts_for, speaking_times
from premium import BTN_PREMIUM, OLD_PREMIUM_BUTTONS, premium_info

LANG = LANGUAGES[EXAM_LANGUAGE]
log = logging.getLogger("bot")

router = Router()
router.message.filter(F.chat.type == "private")

SPEAKING_NAME = LANG["speaking_name"]   # turkcha: "Konuşma"
WRITING_NAME = LANG["writing_name"]     # turkcha: "Yazma"

BTN_SPEAKING = f"🎙 {SPEAKING_NAME}"
BTN_WRITING = f"✍️ {WRITING_NAME}"
BTN_ADMIN = "⚙️ Admin panel"
BTN_RESULTS = "📊 Natijalarim"
# eski klaviaturalar (bot yangilanishidan oldin chiqqan tugmalar) ham ishlashi uchun
OLD_SPEAKING_BUTTONS = {"🎙 Imtihon topshirish", "🎙 Speaking"}
OLD_WRITING_BUTTONS = {"✍️ Writing"}
BTN_OLD_EXAMS = "🎙 Imtihon topshirish"

KIND_ICON = {"speaking": "🎙", "writing": "✍️"}
KIND_NAME = {"speaking": SPEAKING_NAME, "writing": WRITING_NAME}


class NewExam(StatesGroup):
    title = State()


class NewQuestion(StatesGroup):
    text = State()
    photo = State()
    prep = State()
    answer = State()


class WritingTask(StatesGroup):
    text = State()
    photo = State()


class WritingAnswer(StatesGroup):
    text = State()
    retry = State()  # tekshirishda xato bo'ldi - matnlar saqlangan, «🔁 Qayta tekshirish» kutilmoqda


def e(value) -> str:
    return html.escape(str(value or ""), quote=False)


async def _send_chunks(target: Message, text: str, reply_markup=None):
    """Uzun HTML matnni bo'lib yuboradi; tugmalar oxirgi bo'lakka biriktiriladi."""
    chunks = grader.split_message(text)
    for i, chunk in enumerate(chunks):
        await target.answer(chunk, parse_mode="HTML", reply_markup=reply_markup if i == len(chunks) - 1 else None)


def _pkey(part: str | None) -> str:
    """callback_data uchun: turi belgilanmagan eski imtihonlar 'x' bo'ladi."""
    return part or "x"


def _part_from_key(key: str) -> str | None:
    return None if key == "x" else key


def main_keyboard(user_id: int) -> ReplyKeyboardMarkup:
    rows = [[KeyboardButton(text=BTN_SPEAKING), KeyboardButton(text=BTN_WRITING)],
            [KeyboardButton(text=BTN_RESULTS), KeyboardButton(text=BTN_PREMIUM)]]
    if is_admin(user_id):
        rows.append([KeyboardButton(text=BTN_ADMIN)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


# ======================================================================
# O'QUVCHI
# ======================================================================

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await db.save_user(message.from_user.id, message.from_user.full_name, message.from_user.username)
    await message.answer(
        f"{LANG['greeting']}, {e(message.from_user.first_name)}! 👋\n\n"
        f"Bu bot {LANG['name_uz']}dan <b>{SPEAKING_NAME}</b> (gapirish) va <b>{WRITING_NAME}</b> (yozish) ko'nikmalarini "
        "sun'iy intellekt yordamida Bilimni baholash agentligining ko'p darajali "
        "(multilevel) tizimi bo'yicha baholaydi: 75 ballik shkala va daraja.\n\n"
        "Bo'limni tanlang 👇",
        parse_mode="HTML",
        reply_markup=main_keyboard(message.from_user.id),
    )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Bekor qilindi.", reply_markup=main_keyboard(message.from_user.id))


# Menyu tugmalari va buyruqlar har qanday holatda (savol, nom, topshiriq yozilayotganda ham) birinchi ushlanadi -
# aks holda ular test nomi / savol matni sifatida saqlanib qolardi.
@router.message(F.text.in_({BTN_PREMIUM} | OLD_PREMIUM_BUTTONS))
@router.message(Command("premium", "tarif"))
async def menu_premium(message: Message, state: FSMContext):
    await premium_info(message, state)


# ---------- 📊 Natijalarim: o'quvchi o'z natijalarini ko'radi ----------

MY_PER_PAGE = 8
_KIND_CODE = {"speaking": "s", "writing": "w"}


async def _my_results_view(user_id: int, page: int):
    rows = await db.my_results(user_id, MY_PER_PAGE + 1, page * MY_PER_PAGE)
    has_next = len(rows) > MY_PER_PAGE
    rows = rows[:MY_PER_PAGE]
    if not rows and page == 0:
        return ("📊 <b>Natijalarim</b>\n\nHali natijangiz yo'q. «🎙 Konuşma» yoki «✍️ Yazma» bo'limidan test "
                "topshiring — natija shu yerda saqlanadi."), None
    counts = await db.my_result_counts(user_id)
    lines = ["📊 <b>Natijalarim</b>",
             f"Jami: {KIND_ICON['speaking']} {SPEAKING_NAME} — <b>{counts.get('speaking', 0)}</b> ta • "
             f"{KIND_ICON['writing']} {WRITING_NAME} — <b>{counts.get('writing', 0)}</b> ta\n"]
    buttons = []
    for i, r in enumerate(rows, page * MY_PER_PAGE + 1):
        sc, _ = report.score(r)
        part = part_info(r["kind"], r.get("part"))["name"].split(" — ")[0]
        level = f" ({e(r['level'])})" if r.get("level") else ""
        lines.append(f"<b>{i}.</b> {report.local_date(r['created_at'])} • {KIND_ICON.get(r['kind'], '')} {e(part)} • "
                     f"{e(r.get('title') or 'o‘chirilgan test')}\n    🎯 <b>{e(sc)}</b>{level}")
        buttons.append(InlineKeyboardButton(text=f"🔍 {i}", callback_data=f"mr:{_KIND_CODE[r['kind']]}:{r['id']}"))
    lines.append("\n🔍 — natijani to'liq ko'rish (xatolar va tavsiyalar bilan).")
    kb = [buttons[i:i + 4] for i in range(0, len(buttons), 4)]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️ Yangiroq", callback_data=f"mrp:{page - 1}"))
    if has_next:
        nav.append(InlineKeyboardButton(text="Eskiroq ▶️", callback_data=f"mrp:{page + 1}"))
    if nav:
        kb.append(nav)
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=kb)


@router.message(F.text == BTN_RESULTS)
@router.message(Command("natijalar", "results"))
async def my_results(message: Message):
    text, kb = await _my_results_view(message.from_user.id, 0)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("mrp:"))
async def my_results_page(callback: CallbackQuery):
    text, kb = await _my_results_view(callback.from_user.id, max(0, int(callback.data.split(":")[1])))
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("mr:"))
async def my_result_open(callback: CallbackQuery):
    _, code, rid = callback.data.split(":")
    kind = {v: k for k, v in _KIND_CODE.items()}.get(code)
    r = await db.my_result(callback.from_user.id, kind, int(rid)) if kind else None
    if not r:
        return await callback.answer("Natija topilmadi.", show_alert=True)
    await callback.answer()
    try:
        res = json.loads(r["result_json"] or "{}")
        res.setdefault("kind", kind)
        part_name = res.get("part_name") or part_info(kind, r.get("part"))["name"]
        text = grader.result_message(r.get("title") or "o‘chirilgan test", part_name, res)
    except (ValueError, KeyError, TypeError):
        sc, _ = report.score(r)
        text = f"{KIND_ICON.get(kind, '')} <b>{e(r.get('title') or 'Test')}</b>\n🎯 {e(sc)} ({e(r.get('level') or '')})"
    text = f"📅 {report.local_date(r['created_at'])}\n" + text
    await grader.send_long(callback.bot, callback.from_user.id, text)


@router.message(Command("status"))
async def menu_status(message: Message):
    from ops import status  # ops bot.py'ni import qiladi - aylana importdan qochish uchun shu yerda
    await status(message)


@router.message(F.text == BTN_ADMIN)
@router.message(Command("admin"))
async def menu_admin(message: Message, state: FSMContext):
    await state.clear()
    if not _admin_only(message.from_user.id):
        # eski klaviaturada «Admin panel» qolgan oddiy foydalanuvchi - klaviaturani yangilaymiz
        return await message.answer("Bo'limni tanlang 👇", reply_markup=main_keyboard(message.from_user.id))
    text, kb = await _admin_panel_view()
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.message(F.text.startswith("/"))
async def unknown_command(message: Message):
    """Noma'lum buyruq hech qachon nom / matn / izoh sifatida saqlanib qolmasin."""
    await message.answer("Bunday buyruq yo'q. Joriy amalni bekor qilish: /cancel, bosh menyu: /start")


async def _student_catalog(kind: str) -> dict:
    """O'quvchiga ko'rinadigan testlar tur bo'yicha: {qism: [test, ...]}. Faqat ochiq, savoli bor va (yazmada)
    topshirig'i to'liq bo'lganlari. Noma'lum / eski qism qiymatlari «Boshqa» (None) ga tushadi."""
    known = parts_for(kind)
    catalog: dict = {}
    for ex in await db.active_exams_with_task(kind):
        if kind == "writing":
            values = writing.task_values(ex.get("part"), {"text": ex.get("task_text"), "meta": ex.get("task_meta")})
            if writing.missing_fields(ex.get("part"), values):
                continue
        catalog.setdefault(ex["part"] if ex.get("part") in known else None, []).append(ex)
    return catalog


async def _parts_keyboard(kind: str) -> InlineKeyboardMarkup | None:
    catalog = await _student_catalog(kind)
    prefix = "sp" if kind == "speaking" else "wr"
    rows = []
    for key, info in parts_for(kind).items():
        if catalog.get(key):
            rows.append([InlineKeyboardButton(text=f"{info['name']} ({len(catalog[key])})", callback_data=f"{prefix}:{key}")])
    if catalog.get(None):
        rows.append([InlineKeyboardButton(text=f"Boshqa ({len(catalog[None])})", callback_data=f"{prefix}:x")])
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


async def _show_parts(target: Message, kind: str, edit: bool = False):
    kb = await _parts_keyboard(kind)
    if kb is None:
        text = f"Hozircha ochiq «{KIND_NAME[kind]}» topshiriqlari yo'q. Keyinroq qayta urinib ko'ring."
    else:
        text = f"{KIND_ICON[kind]} <b>{KIND_NAME[kind]}</b> — turini tanlang:"
    if edit:
        await target.edit_text(text, parse_mode="HTML", reply_markup=kb)
    else:
        await target.answer(text, parse_mode="HTML", reply_markup=kb)


# ---------- Speaking ----------

@router.message(F.text.in_({BTN_SPEAKING} | OLD_SPEAKING_BUTTONS))
async def speaking_menu(message: Message, state: FSMContext):
    await state.clear()
    await _show_parts(message, "speaking")


@router.callback_query(F.data == "sp_menu")
async def speaking_menu_cb(callback: CallbackQuery, state: FSMContext):
    await state.clear()  # chatda yozilayotgan yazma bekor bo'ladi - keyingi xabar insho deb olinmasin
    await _show_parts(callback.message, "speaking", edit=True)
    await callback.answer()


@router.callback_query(F.data.startswith("sp:"))
async def speaking_part(callback: CallbackQuery, state: FSMContext):
    allowed, reason = await check_access(callback.from_user.id)
    if not allowed:
        return await callback.answer(reason, show_alert=True)
    await state.clear()
    part = _part_from_key(callback.data.split(":", 1)[1])
    info = part_info("speaking", part)
    exams = (await _student_catalog("speaking")).get(part, [])
    rows = [
        [
            InlineKeyboardButton(
                text=f"🎙 {ex['title']}",
                web_app=WebAppInfo(url=f"{WEBAPP_URL}/?exam={ex['id']}&t={int(time.time())}"),
            )
        ]
        for ex in exams
    ]
    rows.append([InlineKeyboardButton(text="⬅️ Turlar", callback_data="sp_menu")])
    about = f"\n{e(info['about'])}" if info.get("about") else ""
    text = (
        f"🎙 <b>{e(info['name'])}</b>{about}\n\n"
        + ("Topshiriqni tanlang. Tinch joyda bo'ling va mikrofonga ruxsat bering 🎧" if exams else "Bu turda hozircha ochiq topshiriq yo'q.")
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


# ---------- Writing ----------

@router.message(F.text.in_({BTN_WRITING} | OLD_WRITING_BUTTONS))
async def writing_menu(message: Message, state: FSMContext):
    await state.clear()
    await _show_parts(message, "writing")


@router.callback_query(F.data == "wr_menu")
async def writing_menu_cb(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await _show_parts(callback.message, "writing", edit=True)
    await callback.answer()


@router.callback_query(F.data == "wr_cancel")
async def writing_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer("Bekor qilindi")
    await _show_parts(callback.message, "writing")


@router.callback_query(F.data.startswith("wr:"))
async def writing_part(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    part = _part_from_key(callback.data.split(":", 1)[1])
    info = part_info("writing", part)
    exams = (await _student_catalog("writing")).get(part, [])
    rows = [[InlineKeyboardButton(text=f"✍️ {ex['title']}", callback_data=f"wt:{ex['id']}")] for ex in exams]
    rows.append([InlineKeyboardButton(text="⬅️ Turlar", callback_data="wr_menu")])
    words = f"\nHajm: <b>{e(info['words'])}</b>" if info.get("words") else ""
    about = f"\n{e(info['about'])}" if info.get("about") else ""
    text = (
        f"✍️ <b>{e(info['name'])}</b>{about}{words}\n\n"
        + ("Mavzuni tanlang:" if exams else "Bu turda hozircha ochiq mavzu yo'q.")
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


async def _open_writing_task(exam_id: int) -> tuple[dict | None, dict | None, dict]:
    """(mavzu, topshiriq qatori, maydonlar) - mavzu ochiq va topshirig'i to'liq bo'lsagina."""
    exam = await db.get_exam(exam_id)
    if not exam or not exam["is_active"] or exam.get("kind") != "writing":
        return None, None, {}
    row = await db.get_writing_task(exam_id)
    values = writing.task_values(exam.get("part"), row)
    if not row or writing.missing_fields(exam.get("part"), values):
        return None, None, {}
    return exam, row, values


def _cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Bekor qilish", callback_data="wr_cancel")]])


@router.callback_query(F.data.startswith("wt:"))
async def writing_topic(callback: CallbackQuery, state: FSMContext):
    """Mavzu tanlandi: topshiriqni ko'rsatib, yozish usulini tanlatadi."""
    allowed, reason = await check_access(callback.from_user.id)
    if not allowed:
        return await callback.answer(reason, show_alert=True)
    exam, row, values = await _open_writing_task(int(callback.data.split(":")[1]))
    if not exam:
        return await callback.answer("Bu mavzu hozir yopiq.", show_alert=True)
    await state.clear()
    info = part_info("writing", exam["part"])
    letters = f", {LANG['lang_adj'].lower()} harflar paneli" if LANG.get("special_chars") else ""
    how = (
        "\n\nQanday yozasiz?\n"
        f"🖥 <b>Imtihon rejimi</b> — taymer ({info.get('time', 60)} daqiqa), so'z hisoblagich{letters}; "
        "matnlarni topshirguncha tahrirlash mumkin, vaqt tugaganda ish o'zi topshiriladi — xuddi imtihondagidek.\n"
        "💬 <b>Chatda</b> — har bir matnni alohida xabar qilib yuborasiz."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🖥 Imtihon rejimida yozish",
            web_app=WebAppInfo(url=f"{WEBAPP_URL}/writing?exam={exam['id']}&t={int(time.time())}"),
        )],
        [InlineKeyboardButton(text="💬 Chatda yozish", callback_data=f"wchat:{exam['id']}")],
        [InlineKeyboardButton(text="⬅️ Mavzular", callback_data=f"wr:{_pkey(exam['part'])}")],
    ])
    if row["photo_file_id"]:
        await callback.message.answer_photo(row["photo_file_id"])
    await _send_chunks(callback.message, writing.task_message(exam["title"], exam["part"], values) + how, kb)
    await callback.answer()


def _ask_component_text(part: str | None, values: dict, step: int) -> str:
    comps = part_info("writing", part)["components"]
    c = comps[step]
    num = f"({step + 1}/{len(comps)}) " if len(comps) > 1 else ""
    _, instruction = writing.component_task(part, values, c["key"])
    hint = f"\n📌 <i>{e(instruction)}</i>\n" if instruction else ""
    return (
        f"✏️ {num}<b>{e(c['name'])}</b> — {e(c['words'])}.{hint}\n"
        "Matnni <b>bitta xabar</b> qilib yuboring. Bekor qilish: /cancel"
    )


@router.callback_query(F.data.startswith("wchat:"))
async def writing_chat_start(callback: CallbackQuery, state: FSMContext):
    allowed, reason = await check_access(callback.from_user.id)
    if not allowed:
        return await callback.answer(reason, show_alert=True)
    exam, _, values = await _open_writing_task(int(callback.data.split(":")[1]))
    if not exam:
        return await callback.answer("Bu mavzu hozir yopiq.", show_alert=True)
    await state.clear()
    await state.set_state(WritingAnswer.text)
    await state.update_data(exam_id=exam["id"], step=0, texts={}, pending=None, started=time.time())
    await callback.message.answer(_ask_component_text(exam["part"], values, 0), parse_mode="HTML", reply_markup=_cancel_kb())
    await callback.answer()


MENU_BUTTONS = (
    {BTN_SPEAKING, BTN_WRITING, BTN_ADMIN, BTN_PREMIUM}
    | OLD_SPEAKING_BUTTONS | OLD_WRITING_BUTTONS | OLD_PREMIUM_BUTTONS
)


def _short_text_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Shunday yuborish", callback_data="wa_ok"),
        InlineKeyboardButton(text="✏️ Qayta yozaman", callback_data="wa_redo"),
    ]])


@router.message(WritingAnswer.text, F.text & ~F.text.startswith("/"))
async def writing_answer(message: Message, state: FSMContext):
    if message.text in MENU_BUTTONS:
        await state.clear()
        return await message.answer(f"{WRITING_NAME} bekor qilindi. Bo'limni qaytadan tanlang.",
                                    reply_markup=main_keyboard(message.from_user.id))

    data = await state.get_data()
    exam = await db.get_exam(data.get("exam_id", 0))
    if not exam:
        await state.clear()
        return await message.answer("Mavzu topilmadi. Qaytadan tanlang.")

    text = message.text.strip()
    n = writing.count_words(text)
    if n < 5:
        return await message.answer("Matn juda qisqa. To'liq javobingizni bitta xabar qilib yuboring.")
    comp = part_info("writing", exam["part"])["components"][data.get("step", 0)]
    await _drop_pending_kb(message, data)  # oldingi tasdiq xabaridagi tugmalar endi kerak emas
    if n < comp.get("wmin", 0):
        # Talabdan ancha qisqa - tasodifan yarim matn yuborilmaganiga ishonch hosil qilamiz
        ask = await message.answer(
            f"{writing.words_note(comp, n)}\n\nShu holicha yuborasizmi? To'ldirmoqchi bo'lsangiz, "
            "«✏️ Qayta yozaman»ni bosing va to'liq matnni yangi xabar qilib yuboring.",
            reply_markup=_short_text_kb(),
        )
        return await state.update_data(pending=text, pending_msg=ask.message_id)
    await state.update_data(pending=None, pending_msg=None)
    await _accept_component(message, state, text, message.from_user)


async def _drop_pending_kb(message: Message, data: dict):
    if data.get("pending_msg"):
        try:
            await message.bot.edit_message_reply_markup(chat_id=message.chat.id, message_id=data["pending_msg"], reply_markup=None)
        except Exception:
            pass


@router.callback_query(WritingAnswer.text, F.data == "wa_ok")
async def writing_answer_confirm(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    text = data.get("pending")
    if not text or data.get("pending_msg") != callback.message.message_id:
        return await callback.answer("Bu tasdiq eskirgan — eng oxirgi xabardagi tugmani bosing.", show_alert=True)
    await callback.answer()
    await state.update_data(pending=None, pending_msg=None)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await _accept_component(callback.message, state, text, callback.from_user)


@router.callback_query(WritingAnswer.text, F.data == "wa_redo")
async def writing_answer_redo(callback: CallbackQuery, state: FSMContext):
    if (await state.get_data()).get("pending_msg") != callback.message.message_id:
        return await callback.answer("Bu tasdiq eskirgan — eng oxirgi xabardagi tugmani bosing.", show_alert=True)
    await state.update_data(pending=None, pending_msg=None)
    await callback.answer()
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.message.answer("✏️ Matnni to'ldirib, bitta xabar qilib qayta yuboring. Bekor qilish: /cancel")


async def _accept_component(target: Message, state: FSMContext, text: str, user):
    """Navbatdagi matn qabul qilindi: keyingisini so'raydi yoki hammasi tayyor bo'lsa - tekshiradi."""
    data = await state.get_data()
    exam = await db.get_exam(data.get("exam_id", 0))
    if not exam:
        await state.clear()
        return await target.answer("Mavzu topilmadi. Qaytadan tanlang.")
    part = exam["part"]
    comps = part_info("writing", part)["components"]
    step = data.get("step", 0)
    texts = dict(data.get("texts") or {})
    texts[comps[step]["key"]] = text
    note = writing.words_note(comps[step], writing.count_words(text))

    if step + 1 < len(comps):  # keyingi matnni so'raymiz
        await state.update_data(step=step + 1, texts=texts, pending=None)
        values = writing.task_values(part, await db.get_writing_task(exam["id"]))
        return await target.answer(
            f"✅ <b>{e(comps[step]['name'])}</b> qabul qilindi. {note}\n\n" + _ask_component_text(part, values, step + 1),
            parse_mode="HTML",
            reply_markup=_cancel_kb(),
        )

    elapsed = int(time.time() - data["started"]) if data.get("started") else None
    await state.update_data(texts=texts, pending=None, elapsed=elapsed)
    await state.set_state(WritingAnswer.retry)  # tekshirish tugaguncha yangi matn qabul qilinmaydi
    await target.answer(f"✅ Qabul qilindi. {note}")
    _in_background(_grade_writing_chat(target, state, user))


_background: set = set()


def _in_background(coro):
    """AI tekshiruvi (20-60 s) fonda: handler darhol tugaydi, shu paytda o'quvchi boshqa tugmalarni bosa oladi."""
    task = asyncio.create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)
    task.add_done_callback(lambda t: t.cancelled() or not t.exception() or
                           log.error("Fondagi tekshiruv xatosi", exc_info=t.exception()))


async def _grade_writing_chat(target: Message, state: FSMContext, user):
    data = await state.get_data()
    exam = await db.get_exam(data.get("exam_id", 0))
    if not exam:
        await state.clear()
        return await target.answer("Mavzu topilmadi. Qaytadan tanlang.")
    wait = await target.answer("⏳ Ishingiz rasmiy mezonlar bo'yicha tekshirilmoqda... (20–60 soniya)")
    try:
        result, fresh = await writing.grade_and_store(
            target.bot, user.id, user.full_name, exam, data.get("texts") or {}, data.get("elapsed")
        )
    except writing.WritingBusy as busy:
        return await wait.edit_text(f"⏳ {busy}")
    except writing.WritingDenied as denied:
        await _clear_if_waiting(state, exam["id"])
        return await wait.edit_text(f"⚠️ {denied}")
    except Exception as err:
        if getattr(err, "status", None) == 429:
            msg = "Hozir tekshirish navbati band. Matnlaringiz saqlandi — 1–2 daqiqadan keyin «🔁 Qayta tekshirish»ni bosing."
        else:
            msg = "Tekshirishda xatolik bo'ldi. Matnlaringiz saqlandi — birozdan keyin «🔁 Qayta tekshirish»ni bosing."
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔁 Qayta tekshirish", callback_data="wa_retry")],
            [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="wr_cancel")],
        ])
        return await wait.edit_text(msg, reply_markup=kb)
    await _clear_if_waiting(state, exam["id"])
    try:
        await wait.delete()
    except Exception:
        pass
    if not fresh:  # aynan shu ish yaqinda tekshirilgan - natija qayta yuboriladi
        info = part_info("writing", exam["part"])
        await grader.send_long(target.bot, user.id, grader.result_message(exam["title"], info["name"], result))


async def _clear_if_waiting(state: FSMContext, exam_id: int):
    """Tekshiruv fonda tugadi: o'quvchi bu orada boshqa amalni boshlagan bo'lsa, uning holatiga tegmaymiz."""
    if await state.get_state() == WritingAnswer.retry.state and (await state.get_data()).get("exam_id") == exam_id:
        await state.clear()


@router.callback_query(WritingAnswer.retry, F.data == "wa_retry")
async def writing_answer_retry(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    _in_background(_grade_writing_chat(callback.message, state, callback.from_user))


@router.message(WritingAnswer.retry)
async def writing_answer_waiting(message: Message, state: FSMContext):
    if message.text in MENU_BUTTONS:
        await state.clear()
        return await message.answer(f"{WRITING_NAME} bekor qilindi. Bo'limni qaytadan tanlang.",
                                    reply_markup=main_keyboard(message.from_user.id))
    exam_id = (await state.get_data()).get("exam_id")
    if (message.from_user.id, exam_id) in writing._inflight:
        return await message.answer("⏳ Ishingiz hozir tekshirilmoqda — natijani bir oz kuting. Bekor qilish: /cancel")
    # tekshiruv ketmayapti (masalan, bot qayta ishga tushdi) - matnlar saqlangan, qayta tekshirish taklif qilinadi
    await message.answer(
        "Ishingiz qabul qilingan, lekin tekshiruv tugallanmagan. «🔁 Qayta tekshirish»ni bosing.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔁 Qayta tekshirish", callback_data="wa_retry")],
            [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="wr_cancel")],
        ]),
    )


@router.message(WritingAnswer.text)
async def writing_answer_wrong(message: Message):
    await message.answer("Javobni <b>matn</b> ko'rinishida yuboring. Bekor qilish: /cancel", parse_mode="HTML")


# ======================================================================
# ADMIN: imtihonlar ro'yxati
# ======================================================================

def _admin_only(user_id: int) -> bool:
    return is_admin(user_id)


def _exam_ready(ex: dict) -> bool:
    """O'quvchilarga ko'rinadimi: konuşmada kamida bitta savol, yazmada topshiriq to'liq."""
    if (ex.get("kind") or "speaking") == "writing":
        values = writing.task_values(ex.get("part"), {"text": ex.get("task_text"), "meta": ex.get("task_meta")})
        return bool(ex.get("task_text") is not None) and not writing.missing_fields(ex.get("part"), values)
    return bool(ex.get("q_count"))


async def _with_task(exam: dict) -> dict:
    qs = await db.get_questions(exam["id"])
    first = qs[0] if qs else {}
    return {**exam, "q_count": len(qs), "task_text": first.get("text"), "task_meta": first.get("meta")}


async def _admin_panel_view():
    """Admin bosh sahifasi: qisqa ko'rsatkichlar va bo'limlar."""
    exams = await db.list_exams()
    ready = sum(1 for ex in exams if _exam_ready(ex))
    users = await db.count_users()
    premium = await db.count_active_premium()
    text = (
        "⚙️ <b>Admin panel</b>\n\n"
        f"📚 Testlar: <b>{len(exams)}</b> (o'quvchilarga ko'rinadi: {ready})\n"
        f"👥 Foydalanuvchilar: <b>{users}</b> • 💎 Obunachilar: <b>{premium}</b>"
    )
    B = InlineKeyboardButton
    rows = [
        [B(text="📚 Testlar", callback_data="adm_exams"), B(text="👥 Foydalanuvchilar", callback_data="adm_users:0")],
        [B(text="💎 Premium", callback_data="adm_prem"), B(text="📊 Natijalar", callback_data="results:0")],
        [B(text="📣 Xabar yuborish", callback_data="adm_bc"), B(text="📈 Statistika", callback_data="adm_stats")],
        [B(text="👮 Adminlar", callback_data="adm_admins"), B(text="⚙️ Sozlamalar", callback_data="adm_settings")],
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def _join_limited(lines: list[str], limit: int = 3900) -> str:
    """Qatorlarni Telegram chegarasidan oshirmay birlashtiradi (HTML teglar qator ichida yopiladi)."""
    out, size = [], 0
    for i, line in enumerate(lines):
        if size + len(line) + 1 > limit:
            out.append(f"… va yana {len(lines) - i} qator")
            break
        out.append(line)
        size += len(line) + 1
    return "\n".join(out)


async def _exams_view():
    exams = await db.list_exams()
    lines = ["📚 <b>Testlar</b>"]
    rows = []
    if not exams:
        lines.append("\nHali test yo'q. Quyidagi tugmalar bilan birinchisini yarating.")
    for kind in ("speaking", "writing"):
        group = [ex for ex in exams if (ex.get("kind") or "speaking") == kind]
        if not group:
            continue
        lines.append(f"\n{KIND_ICON[kind]} <b>{KIND_NAME[kind]}</b>")
        for ex in group:
            status = "✅" if _exam_ready(ex) else "❗"
            part = part_info(kind, ex.get("part"))["name"]
            lines.append(f"{status} <b>{e(ex['title'])}</b> — {e(part)}")
            rows.append(
                [InlineKeyboardButton(text=f"{status} {KIND_ICON[kind]} {ex['title']}"[:60], callback_data=f"exam:{ex['id']}")]
            )
    if exams:
        lines.append("\n✅ — o'quvchilarga ko'rinadi, ❗ — hali to'ldirilmagan (savol / topshiriq yo'q)")
    rows.append([
        InlineKeyboardButton(text=f"➕ 🎙 {SPEAKING_NAME} testi", callback_data="nk:speaking"),
        InlineKeyboardButton(text=f"➕ ✍️ {WRITING_NAME} mavzusi", callback_data="nk:writing"),
    ])
    rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="admin")])
    return _join_limited(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def _preview(text: str, limit: int = 300) -> str:
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


async def _writing_task_view(exam: dict) -> tuple[list[str], list[list[InlineKeyboardButton]]]:
    """Yazma mavzusi: topshiriq qismlari holati (PDF talablariga mosligi) va tahrirlash tugmalari."""
    exam_id, part = exam["id"], exam.get("part")
    row = await db.get_writing_task(exam_id)
    values = writing.task_values(part, row)
    fields = writing.fields_for(part)
    missing = writing.missing_fields(part, values)
    lines = ["<b>Topshiriq qismlari:</b>"]
    for i, f in enumerate(fields, 1):
        spec = writing.FIELDS[f]
        value = values.get(f)
        if value:
            ok, note = writing.field_note(f, value)
            lines.append(f"\n{i}. {'✅' if ok else '⚠️'} <b>{e(spec['short'])}</b> — {note}\n<i>{e(_preview(value))}</i>")
        else:
            lines.append(f"\n{i}. ❗ <b>{e(spec['short'])}</b> — kiritilmagan")
    has_photo = bool(row and row.get("photo_file_id"))
    lines.append("\n🖼 Rasm: " + ("bor" if has_photo else "yo'q (ixtiyoriy)"))
    if missing:
        lines.append("\n❗ Topshiriq to'liq emas — yetishmayotgan qismlarni kiriting, shundan keyin o'quvchilar uni ko'radi.")
    else:
        lines.append("\n💡 «👁 O'quvchi ko'rinishi» tugmasi bilan o'quvchi ko'radigan topshiriqni tekshiring.")

    rows = []
    edit = [InlineKeyboardButton(text=f"✏️ {writing.FIELDS[f]['short']}", callback_data=f"wtf:{exam_id}:{f}") for f in fields]
    rows += [edit[i:i + 2] for i in range(0, len(edit), 2)]
    if len(fields) > 1:
        label = "📝 Bosqichma-bosqich kiritish" if len(missing) == len(fields) else "📝 Hammasini qaytadan kiritish"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"wtask:{exam_id}")])
    photo_row = [InlineKeyboardButton(text="🖼 Rasmni almashtirish" if has_photo else "🖼 Rasm qo'shish", callback_data=f"wph:{exam_id}")]
    if has_photo:
        photo_row.append(InlineKeyboardButton(text="🗑 Rasmni olib tashlash", callback_data=f"wphx:{exam_id}"))
    rows.append(photo_row)
    if not missing:
        rows.append([InlineKeyboardButton(text="👁 O'quvchi ko'rinishi", callback_data=f"wprev:{exam_id}")])
    return lines, rows


async def _exam_view(exam_id: int):
    exam = await db.get_exam(exam_id)
    if not exam:
        return "Imtihon topilmadi.", InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⬅️ Testlar", callback_data="adm_exams")]]
        )
    kind = exam.get("kind") or "speaking"
    info = part_info(kind, exam.get("part"))
    status = ("✅ O'quvchilarga ko'rinadi" if _exam_ready(await _with_task(exam))
              else "❗ Hali o'quvchilarga ko'rinmaydi — " + ("topshiriqni to'ldiring" if kind == "writing" else "savol qo'shing"))
    lines = [f"{KIND_ICON[kind]} <b>{e(exam['title'])}</b>\n{KIND_NAME[kind]} • {e(info['name'])}\n{status}\n"]
    rows = []

    if kind == "writing":
        task_lines, task_rows = await _writing_task_view(exam)
        lines += task_lines
        rows += task_rows
    else:
        questions = await db.get_questions(exam_id)
        if info.get("questions_hint"):
            lines.append(f"💡 {e(info['questions_hint'])}\n")
        if info.get("count") and questions and len(questions) != info["count"]:
            lines.append(f"⚠️ Rasmiy formatda bu qismda {info['count']} ta savol bo'ladi (hozir: {len(questions)}).\n")
        if not questions:
            lines.append("Savollar yo'q. «➕ Savol qo'shish» tugmasini bosing.")
        for i, q in enumerate(questions, 1):
            photo = " 🖼" if q["photo_file_id"] else ""
            lines.append(f"<b>{i}.</b>{photo} {e(_preview(q['text']))}\n    ⏳ {q['prep_sec']} s, 🎙 {q['answer_sec']} s")
        if questions:
            lines.append("\n✏️ Savolni o'zgartirish (matn, rasm, vaqt, tartib, o'chirish) uchun uning tugmasini bosing.")
        edit = [InlineKeyboardButton(text=f"✏️ {i}-savol", callback_data=f"q_edit:{q['id']}")
                for i, q in enumerate(questions, 1)]
        rows += [edit[i:i + 3] for i in range(0, len(edit), 3)]
        add = [InlineKeyboardButton(text="➕ Savol qo'shish", callback_data=f"q_new:{exam_id}")]
        if len(questions) > 1:
            add.append(InlineKeyboardButton(text="⏱ Hamma savollar vaqti", callback_data=f"qall:{exam_id}"))
        rows.append(add)

    rows.append([InlineKeyboardButton(text="✏️ Nomini o'zgartirish", callback_data=f"exam_ren:{exam_id}")])
    rows.append([InlineKeyboardButton(text="🗑 O'chirish", callback_data=f"exam_del_ask:{exam_id}")])
    rows.append([InlineKeyboardButton(text="⬅️ Testlar", callback_data="adm_exams")])
    return _join_limited(lines), InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "admin")
async def admin_panel_cb(callback: CallbackQuery, state: FSMContext):
    if not _admin_only(callback.from_user.id):
        return await callback.answer()
    await state.clear()
    text, kb = await _admin_panel_view()
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "adm_exams")
async def exams_list_cb(callback: CallbackQuery, state: FSMContext):
    if not _admin_only(callback.from_user.id):
        return await callback.answer()
    await state.clear()
    text, kb = await _exams_view()
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("exam:"))
async def open_exam(callback: CallbackQuery):
    if not _admin_only(callback.from_user.id):
        return await callback.answer()
    text, kb = await _exam_view(int(callback.data.split(":")[1]))
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


# ---------- Yangi test / mavzu: bo'lim -> tur -> nom -> savollar / topshiriq ----------

@router.callback_query(F.data == "exam_new")
async def new_exam(callback: CallbackQuery, state: FSMContext):
    """Eski xabarlardagi «➕ Yangi imtihon / mavzu» tugmasi uchun: bo'limni tanlash."""
    if not _admin_only(callback.from_user.id):
        return await callback.answer()
    await state.clear()
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=f"🎙 {SPEAKING_NAME}", callback_data="nk:speaking"),
                InlineKeyboardButton(text=f"✍️ {WRITING_NAME}", callback_data="nk:writing"),
            ],
            [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="adm_exams")],
        ]
    )
    await callback.message.edit_text("Qaysi bo'lim uchun?", reply_markup=kb)
    await callback.answer()


def _new_title(kind: str) -> str:
    return f"{KIND_ICON[kind]} <b>Yangi {KIND_NAME[kind]} {'mavzusi' if kind == 'writing' else 'testi'}</b>"


@router.callback_query(F.data.startswith("nk:"))
async def new_exam_kind(callback: CallbackQuery, state: FSMContext):
    if not _admin_only(callback.from_user.id):
        return await callback.answer()
    kind = callback.data.split(":")[1]
    if kind not in KIND_NAME:
        return await callback.answer()
    await state.clear()
    await state.update_data(kind=kind)
    lines = [f"{_new_title(kind)} — 1/3: turini tanlang\n"]
    for info in parts_for(kind).values():
        time_note = f" ⏱ ~{info['time']} daqiqa." if kind == "writing" and info.get("time") else ""
        lines.append(f"• <b>{e(info['name'])}</b> — {e(info['about'])}{time_note}")
    rows = [[InlineKeyboardButton(text=info["name"], callback_data=f"np:{kind}:{key}")] for key, info in parts_for(kind).items()]
    rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="adm_exams")])
    await callback.message.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@router.callback_query(F.data.startswith("np:"))
async def new_exam_part(callback: CallbackQuery, state: FSMContext):
    if not _admin_only(callback.from_user.id):
        return await callback.answer()
    bits = callback.data.split(":", 2)
    if len(bits) == 3:  # np:<kind>:<part> - tur tugmaning o'zida (holat yo'qolsa ham to'g'ri ishlaydi)
        kind, part = bits[1], bits[2]
    else:  # eski xabarlardagi np:<part>
        kind, part = (await state.get_data()).get("kind", "speaking"), bits[1]
    if kind not in KIND_NAME or part not in parts_for(kind):
        return await callback.answer("Qaytadan boshlang: «📚 Testlar».", show_alert=True)
    await state.clear()
    await state.update_data(kind=kind, part=part)
    await state.set_state(NewExam.title)
    name = e(part_info(kind, part)["name"])
    if kind == "writing":
        n = len(writing.fields_for(part))
        then = (f"Keyin (3/3) topshiriq {n} ta qismdan iborat bo'lib, har biri alohida so'raladi."
                if n > 1 else "Keyin (3/3) topshiriq matnini yuborasiz.")
        text = (
            f"{_new_title(kind)} — 2/3: nom\n{name}\n\n"
            "Mavzu nomini yozing — o'quvchilar uni mavzular ro'yxatida ko'radi.\n"
            f"Masalan: <i>Sayohat klubi</i>\n\n{then}"
        )
    else:
        text = (
            f"{_new_title(kind)} — 2/3: nom\n{name}\n\n"
            "Test nomini yozing. Masalan: <i>Mock #1</i>\n\nKeyin (3/3) savollarni qo'shasiz."
        )
    await callback.message.edit_text(text, parse_mode="HTML")
    await callback.answer()


@router.message(NewExam.title)
async def new_exam_title(message: Message, state: FSMContext):
    title = (message.text or "").strip()
    if not title or title.startswith("/"):
        return await message.answer("Nomni matn ko'rinishida yozing. Bekor qilish: /cancel")
    data = await state.get_data()
    kind = data.get("kind", "speaking")
    exam_id = await db.create_exam(title[:100], kind, data.get("part"))
    await state.clear()

    if kind == "writing":
        n = len(writing.fields_for(data.get("part")))
        intro = "✅ Mavzu yaratildi — topshiriq to'liq kiritilgach, o'quvchilar uni ko'radi.\n\n3/3: "
        if n > 1:
            intro += (f"topshiriq <b>{n} ta qism</b>dan iborat — har birini alohida xabar qilib yuborasiz, har biri "
                      "yuborilishi bilan saqlanadi. Keyin istalgan qismni alohida tahrirlash mumkin.")
        else:
            intro += "topshiriq matni."
        return await _start_task_flow(message, state, exam_id, data.get("part"), intro=intro)

    await _ask_question_text(message, state, exam_id, intro="✅ Test yaratildi — savol qo'shilishi bilan o'quvchilar uni ko'radi.\n\n3/3: savollar.\n\n")


# ---------- Yazma topshirig'i: qismlab kiritish (PDF tuzilishi bo'yicha) ----------

def _field_prompt(field: str, i: int, total: int) -> str:
    spec = writing.FIELDS[field]
    step = f"{i + 1}/{total}: " if total > 1 else ""
    lines = [f"📝 <b>Topshiriq — {step}{e(spec['title'])}</b>", "", spec["hint"]]
    example = LANG.get(spec["example"]) if spec.get("example") else None
    if example:
        lines += ["", "Namuna:", f"<i>{e(example)}</i>"]
    lines += [
        "",
        f"Matnni <b>{LANG['name_uz']}da</b>, bitta xabar qilib yuboring (ko'pi bilan {spec['limit']} belgi). "
        "Bekor qilish: /cancel",
    ]
    return "\n".join(lines)


async def _start_task_flow(message: Message, state: FSMContext, exam_id: int, part: str | None,
                           fields: list[str] | None = None, intro: str = ""):
    """`fields` - so'raladigan qismlar (standart: hammasi; bittasi - alohida tahrirlash)."""
    fields = fields or writing.fields_for(part)
    await state.clear()
    await state.set_state(WritingTask.text)
    await state.update_data(exam_id=exam_id, queue=fields, ti=0)
    await message.answer((f"{intro}\n\n" if intro else "") + _field_prompt(fields[0], 0, len(fields)), parse_mode="HTML")


@router.callback_query(F.data.startswith("wtask:"))
async def writing_task_edit(callback: CallbackQuery, state: FSMContext):
    if not _admin_only(callback.from_user.id):
        return await callback.answer()
    exam = await db.get_exam(int(callback.data.split(":")[1]))
    if not exam:
        return await callback.answer("Mavzu topilmadi.", show_alert=True)
    n = len(writing.fields_for(exam.get("part")))
    intro = f"♻️ Topshiriqning {n} ta qismi navbat bilan so'raladi; har biri yuborilishi bilan saqlanadi." if n > 1 else ""
    await _start_task_flow(callback.message, state, exam["id"], exam.get("part"), intro=intro)
    await callback.answer()


@router.callback_query(F.data.startswith("wtf:"))
async def writing_field_edit(callback: CallbackQuery, state: FSMContext):
    if not _admin_only(callback.from_user.id):
        return await callback.answer()
    _, exam_id, field = callback.data.split(":", 2)
    exam = await db.get_exam(int(exam_id))
    if not exam or field not in writing.fields_for(exam.get("part")):
        return await callback.answer("Mavzu topilmadi.", show_alert=True)
    current = writing.task_values(exam.get("part"), await db.get_writing_task(exam["id"])).get(field)
    await _start_task_flow(callback.message, state, exam["id"], exam.get("part"), [field])
    if current:
        await callback.message.answer(f"Hozirgi matn (bosib nusxa olish mumkin):\n<code>{e(current)}</code>", parse_mode="HTML")
    await callback.answer()


@router.message(WritingTask.text)
async def writing_task_save(message: Message, state: FSMContext):
    if not _admin_only(message.from_user.id):
        return
    text = (message.text or message.caption or "").strip()
    if not text or text.startswith("/"):
        return await message.answer("Matn yuboring (rasm bo'lsa - izoh sifatida). Bekor qilish: /cancel")
    data = await state.get_data()
    exam = await db.get_exam(data.get("exam_id", 0))
    queue, i = data.get("queue") or [], data.get("ti", 0)
    if not exam or i >= len(queue):
        await state.clear()
        return await message.answer("Mavzu topilmadi. «📚 Testlar» bo'limidan qayta oching.")
    field = queue[i]
    spec = writing.FIELDS[field]
    if len(text) > spec["limit"]:
        return await message.answer(
            f"Matn juda uzun ({len(text)} belgi). {spec['limit']} belgidan oshmasin — qisqartirib qayta yuboring."
        )

    part = exam.get("part")
    values = writing.task_values(part, await db.get_writing_task(exam["id"]))
    values[field] = text
    await db.save_writing_task(exam["id"], writing.compose(part, values), json.dumps(values, ensure_ascii=False))
    if message.photo:  # rasm izoh bilan yuborilgan bo'lsa - topshiriq rasmi sifatida saqlanadi
        await db.set_writing_photo(exam["id"], message.photo[-1].file_id)
    ok, note = writing.field_note(field, text)
    ack = f"✅ <b>{e(spec['short'])}</b> saqlandi. {note}"
    if not ok:
        ack += f"\nTavsiyaga to'liq mos emas — kerak bo'lsa, keyin «✏️ {e(spec['short'])}» tugmasi bilan tahrirlang."

    if i + 1 < len(queue):
        await state.update_data(ti=i + 1)
        return await message.answer(f"{ack}\n\n{_field_prompt(queue[i + 1], i + 1, len(queue))}", parse_mode="HTML")
    await state.clear()
    await message.answer(ack, parse_mode="HTML")
    view, kb = await _exam_view(exam["id"])
    await message.answer(view, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("wph:"))
async def writing_photo_ask(callback: CallbackQuery, state: FSMContext):
    if not _admin_only(callback.from_user.id):
        return await callback.answer()
    if not await db.get_exam(int(callback.data.split(":")[1])):
        return await callback.answer("Mavzu topilmadi (o'chirilgan bo'lishi mumkin).", show_alert=True)
    await state.clear()
    await state.set_state(WritingTask.photo)
    await state.update_data(exam_id=int(callback.data.split(":")[1]))
    await callback.message.answer(
        "🖼 Topshiriq uchun rasmni yuboring. (Ixtiyoriy: rasmiy formatdagi yozma topshiriqlarda odatda rasm "
        "bo'lmaydi.) Bekor qilish: /cancel"
    )
    await callback.answer()


@router.message(WritingTask.photo, F.photo)
async def writing_task_photo(message: Message, state: FSMContext):
    if not _admin_only(message.from_user.id):
        return
    exam_id = (await state.get_data()).get("exam_id", 0)
    await state.clear()
    if not await db.get_exam(exam_id):
        return await message.answer("Mavzu topilmadi (o'chirilgan bo'lishi mumkin).")
    await db.set_writing_photo(exam_id, message.photo[-1].file_id)
    view, kb = await _exam_view(exam_id)
    await message.answer("✅ Rasm saqlandi.")
    await message.answer(view, parse_mode="HTML", reply_markup=kb)


@router.message(WritingTask.photo)
async def writing_task_photo_wrong(message: Message):
    await message.answer("Rasm yuboring. Bekor qilish: /cancel")


@router.callback_query(F.data.startswith("wphx:"))
async def writing_photo_remove(callback: CallbackQuery):
    if not _admin_only(callback.from_user.id):
        return await callback.answer()
    exam_id = int(callback.data.split(":")[1])
    if not await db.get_writing_task(exam_id):
        return await callback.answer("Mavzu topilmadi (o'chirilgan bo'lishi mumkin).", show_alert=True)
    await db.set_writing_photo(exam_id, None)
    view, kb = await _exam_view(exam_id)
    await callback.message.edit_text(view, parse_mode="HTML", reply_markup=kb)
    await callback.answer("Rasm olib tashlandi")


@router.callback_query(F.data.startswith("wprev:"))
async def writing_preview(callback: CallbackQuery):
    if not _admin_only(callback.from_user.id):
        return await callback.answer()
    exam = await db.get_exam(int(callback.data.split(":")[1]))
    if not exam:
        return await callback.answer("Mavzu topilmadi.", show_alert=True)
    row = await db.get_writing_task(exam["id"])
    values = writing.task_values(exam.get("part"), row)
    if row and row.get("photo_file_id"):
        await callback.message.answer_photo(row["photo_file_id"])
    await _send_chunks(
        callback.message, "👁 <b>O'quvchi ko'rinishi</b>\n\n" + writing.task_message(exam["title"], exam.get("part"), values)
    )
    await callback.answer()


# ---------- O'chirish / natijalar ----------

@router.callback_query(F.data.startswith("exam_del_ask:"))
async def delete_exam_ask(callback: CallbackQuery):
    if not _admin_only(callback.from_user.id):
        return await callback.answer()
    exam_id = int(callback.data.split(":")[1])
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Ha, o'chirish", callback_data=f"exam_del:{exam_id}"),
                InlineKeyboardButton(text="❌ Yo'q", callback_data=f"exam:{exam_id}"),
            ]
        ]
    )
    await callback.message.edit_text("Butunlay o'chiriladi. Davom etasizmi?", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("exam_del:"))
async def delete_exam(callback: CallbackQuery):
    if not _admin_only(callback.from_user.id):
        return await callback.answer()
    await db.delete_exam(int(callback.data.split(":")[1]))
    text, kb = await _exams_view()
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer("O'chirildi")


RESULTS_PER_PAGE = 10


@router.callback_query(F.data.startswith("results:"))
async def show_results(callback: CallbackQuery):
    if not _admin_only(callback.from_user.id):
        return await callback.answer()
    page = max(0, int(callback.data.split(":")[1]))
    rows = await db.recent_results(RESULTS_PER_PAGE + 1, page * RESULTS_PER_PAGE)
    has_next = len(rows) > RESULTS_PER_PAGE
    rows = rows[:RESULTS_PER_PAGE]
    if not rows:
        text = "Hali hech kim topshirmagan." if page == 0 else "Bu sahifada natija yo'q."
    else:
        lines = [f"📊 <b>Natijalar</b> (sahifa {page + 1})\n"]
        for r in rows:
            try:
                res = json.loads(r["result_json"] or "{}")
                score = f"{float(res['raw']):g}/{res['max_raw']}"
            except (ValueError, KeyError, TypeError):
                score = f"{r['score']}/75"
            lines.append(
                f"{KIND_ICON.get(r['kind'], '')} {e(r['full_name'] or '?')} — "
                f"<b>{score}</b> ({e(r['level'])}), {e(r['title'] or 'o‘chirilgan test')}"
            )
        text = "\n".join(lines)
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"results:{page - 1}"))
    if has_next:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"results:{page + 1}"))
    pdf = [InlineKeyboardButton(text=f"📄 PDF: {label}", callback_data=f"rpdf:{days}") for days, label in PDF_PERIODS]
    kb_rows = ([nav] if nav else []) + [pdf[:2], pdf[2:]] + [[InlineKeyboardButton(text="⬅️ Orqaga", callback_data="admin")]]
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await callback.answer()


PDF_PERIODS = [(7, "oxirgi 7 kun"), (30, "oxirgi 30 kun"), (0, "hammasi")]
_pdf_busy: set[int] = set()


@router.callback_query(F.data.startswith("rpdf:"))
async def results_pdf(callback: CallbackQuery):
    """Natijalar PDF hisoboti: o'quvchilar alifbo tartibida + har biri bo'yicha batafsil."""
    if not _admin_only(callback.from_user.id):
        return await callback.answer()
    if callback.from_user.id in _pdf_busy:
        return await callback.answer("PDF tayyorlanmoqda, biroz kuting…")
    days = int(callback.data.split(":")[1] or 0)
    period = dict(PDF_PERIODS).get(days, f"oxirgi {days} kun")
    _pdf_busy.add(callback.from_user.id)
    try:
        await callback.answer("📄 PDF tayyorlanmoqda…")
        rows = await db.results_report(days or None)
        data = await asyncio.to_thread(report.build_pdf, rows, period)
        stamp = time.strftime("%Y-%m-%d")
        await callback.message.answer_document(
            BufferedInputFile(data, filename=f"natijalar_{stamp}_{'hammasi' if not days else f'{days}kun'}.pdf"),
            caption=f"📄 Natijalar hisoboti — {period}: {len(rows)} ta natija, "
                    f"{len({r['telegram_id'] for r in rows})} ta o'quvchi.",
        )
    finally:
        _pdf_busy.discard(callback.from_user.id)


# ---------- Nomini o'zgartirish ----------

class RenameExam(StatesGroup):
    title = State()


@router.callback_query(F.data.startswith("exam_ren:"))
async def rename_exam_ask(callback: CallbackQuery, state: FSMContext):
    if not _admin_only(callback.from_user.id):
        return await callback.answer()
    exam_id = int(callback.data.split(":")[1])
    await state.clear()
    await state.set_state(RenameExam.title)
    await state.update_data(exam_id=exam_id)
    await callback.message.answer("Yangi nomni yozing (ko'pi bilan 100 belgi). Bekor qilish: /cancel")
    await callback.answer()


@router.message(RenameExam.title)
async def rename_exam_save(message: Message, state: FSMContext):
    if not _admin_only(message.from_user.id):
        return
    title = (message.text or "").strip()
    if not title or title.startswith("/"):
        return await message.answer("Nomni matn ko'rinishida yozing. Bekor qilish: /cancel")
    exam_id = (await state.get_data())["exam_id"]
    await state.clear()
    await db.rename_exam(exam_id, title[:100])
    text, kb = await _exam_view(exam_id)
    await message.answer("✅ Nomi o'zgartirildi.")
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


# ======================================================================
# ADMIN: speaking savollari - qo'shish (ketma-ket) va tahrirlash
# ======================================================================

SKIP_PHOTO = InlineKeyboardMarkup(
    inline_keyboard=[[InlineKeyboardButton(text="Rasmsiz davom etish ➡️", callback_data="q_nophoto")]]
)
PREP_OPTIONS = [0, 5, 10, 15, 30, 60]
ANSWER_OPTIONS = [30, 45, 60, 90, 120]
PREP_RANGE, ANSWER_RANGE = (0, 300), (10, 600)


class EditQuestion(StatesGroup):
    text = State()
    photo = State()
    prep = State()
    answer = State()


def _seconds_kb(prefix: str, options: list[int], recommended: int | None,
                extra: list[list[InlineKeyboardButton]] | None = None) -> InlineKeyboardMarkup:
    opts = sorted(set(options + ([recommended] if recommended is not None else [])))
    buttons = [InlineKeyboardButton(text=f"{'⭐ ' if s == recommended else ''}{s} s", callback_data=f"{prefix}:{s}")
               for s in opts]
    return InlineKeyboardMarkup(inline_keyboard=[buttons[i:i + 4] for i in range(0, len(buttons), 4)] + (extra or []))


def _parse_seconds(text: str | None, bounds: tuple[int, int]) -> int | None:
    digits = "".join(ch for ch in (text or "") if ch.isdigit())
    if not digits or len(digits) > 4:
        return None
    value = int(digits)
    return value if bounds[0] <= value <= bounds[1] else None


def _asks_photo(part: str | None) -> bool:
    """Part 1.1 - shaxsiy savollar, rasm bo'lmaydi: bu bosqich so'ralmaydi (kerak bo'lsa tahrirlashda qo'shiladi)."""
    return part != "1.1"


async def _ask_question_text(message: Message, state: FSMContext, exam_id: int, intro: str = ""):
    exam = await db.get_exam(exam_id)
    if not exam:
        await state.clear()
        return await message.answer("Test topilmadi (o'chirilgan bo'lishi mumkin).")
    questions = await db.get_questions(exam_id)
    position = len(questions) + 1
    count = part_info("speaking", exam.get("part")).get("count")
    data = await state.get_data()
    last_times = data.get("last_times") if data.get("exam_id") == exam_id else None
    await state.clear()
    await state.set_state(NewQuestion.text)
    await state.update_data(exam_id=exam_id, part=exam.get("part"), position=position, last_times=last_times)
    of = f" (jami {count} ta)" if count else ""
    kb = None
    if questions:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Tugatish", callback_data=f"q_done:{exam_id}")]])
    await message.answer(
        f"{intro}✏️ <b>{position}-savol</b>{of} matnini yozing ({LANG['name_uz']}da).\n"
        f"Masalan: <i>{e(LANG['question_example'])}</i>\n\nBekor qilish: /cancel",
        parse_mode="HTML", reply_markup=kb,
    )


@router.callback_query(F.data.startswith("q_new:"))
async def new_question(callback: CallbackQuery, state: FSMContext):
    if not _admin_only(callback.from_user.id):
        return await callback.answer()
    exam_id = int(callback.data.split(":")[1])
    if not await db.get_exam(exam_id):
        return await callback.answer("Test topilmadi (o'chirilgan bo'lishi mumkin).", show_alert=True)
    await _ask_question_text(callback.message, state, exam_id)
    await callback.answer()


@router.callback_query(F.data.startswith("q_done:"))
async def new_question_done(callback: CallbackQuery, state: FSMContext):
    if not _admin_only(callback.from_user.id):
        return await callback.answer()
    await state.clear()
    await callback.message.edit_reply_markup(reply_markup=None)
    text, kb = await _exam_view(int(callback.data.split(":")[1]))
    await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.message(NewQuestion.text)
async def new_question_text(message: Message, state: FSMContext):
    if not _admin_only(message.from_user.id):
        return
    text = (message.text or "").strip()
    if not text or text.startswith("/"):
        return await message.answer("Savolni matn ko'rinishida yozing. Bekor qilish: /cancel")
    await state.update_data(text=text[:2500])
    data = await state.get_data()
    if not _asks_photo(data.get("part")):
        await state.update_data(photo=None)
        return await _ask_times(message, state)
    await state.set_state(NewQuestion.photo)
    hint = "🖼 Savolga rasm kerak bo'lsa, rasmni yuboring."
    if part_info("speaking", data.get("part")).get("carry_photo") and data.get("position", 1) > 1:
        hint += " Yangi rasm shart emas — oldingi savoldagi rasm bu savolda ham ko'rinadi."
    await message.answer(hint, reply_markup=SKIP_PHOTO)


async def _ask_times(message: Message, state: FSMContext):
    """Vaqtlar: rasmiy (⭐), oldingi savoldagidek yoki boshqa (admin o'zi tanlaydi)."""
    data = await state.get_data()
    prep, answer = speaking_times(data.get("part"), data.get("position", 1))
    rows = [[InlineKeyboardButton(text=f"⭐ Rasmiy: ⏳ {prep} s, 🎙 {answer} s", callback_data=f"q_t:{prep}:{answer}")]]
    last = data.get("last_times")
    if last and tuple(last) != (prep, answer):
        rows.append([InlineKeyboardButton(text=f"↩️ Oldingidek: ⏳ {last[0]} s, 🎙 {last[1]} s",
                                          callback_data=f"q_t:{last[0]}:{last[1]}")])
    rows.append([InlineKeyboardButton(text="⏱ Boshqa vaqt", callback_data="q_other")])
    await state.set_state(NewQuestion.prep)
    await state.update_data(times_menu=True)
    await message.answer("⏱ Vaqtni tanlang (⏳ tayyorlanish, 🎙 javob berish):",
                         reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


async def _ask_prep(message: Message, state: FSMContext):
    data = await state.get_data()
    prep, _ = speaking_times(data.get("part"), data.get("position", 1))
    await state.set_state(NewQuestion.prep)
    await state.update_data(times_menu=False)
    await message.answer("⏳ Tayyorlanish vaqti (⭐ — rasmiy). Boshqa son kerak bo'lsa, yozib yuboring (0–300):",
                         reply_markup=_seconds_kb("q_prep", PREP_OPTIONS, prep))


@router.message(NewQuestion.photo, F.photo)
async def new_question_photo(message: Message, state: FSMContext):
    await state.update_data(photo=message.photo[-1].file_id, album=message.media_group_id)
    if message.media_group_id:
        await message.answer("ℹ️ Bir nechta rasm (albom) yuborildi — savolga faqat birinchisi biriktirildi. "
                             "Ikki rasmni ko'rsatish uchun ularni bitta rasmga birlashtirib yuboring.")
    await _ask_times(message, state)


@router.message(NewQuestion.prep, F.photo)
@router.message(NewQuestion.answer, F.photo)
async def new_question_album_rest(message: Message, state: FSMContext):
    """Albomning qolgan rasmlari keyingi bosqichga kelib qoladi - ularni jim o'tkazib yuboramiz."""
    if message.media_group_id and message.media_group_id == (await state.get_data()).get("album"):
        return
    await message.answer("Yuqoridagi tugmalardan vaqtni tanlang. Bekor qilish: /cancel")


@router.callback_query(NewQuestion.photo, F.data == "q_nophoto")
async def new_question_nophoto(callback: CallbackQuery, state: FSMContext):
    await state.update_data(photo=None)
    await callback.message.edit_reply_markup(reply_markup=None)
    await _ask_times(callback.message, state)
    await callback.answer()


@router.message(NewQuestion.photo)
async def new_question_photo_wrong(message: Message):
    await message.answer("Rasm yuboring yoki «Rasmsiz davom etish» tugmasini bosing.", reply_markup=SKIP_PHOTO)


@router.callback_query(NewQuestion.prep, F.data == "q_other")
async def new_question_other(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_reply_markup(reply_markup=None)
    await _ask_prep(callback.message, state)
    await callback.answer()


@router.callback_query(NewQuestion.prep, F.data.startswith("q_t:"))
async def new_question_times(callback: CallbackQuery, state: FSMContext):
    _, prep, answer = callback.data.split(":")
    await callback.message.edit_reply_markup(reply_markup=None)
    await state.update_data(prep=int(prep))
    await _save_new_question(callback.message, state, int(answer))
    await callback.answer()


async def _set_prep(message: Message, state: FSMContext, prep: int):
    data = await state.get_data()
    _, answer = speaking_times(data.get("part"), data.get("position", 1))
    await state.update_data(prep=prep)
    await state.set_state(NewQuestion.answer)
    await message.answer(f"⏳ Tayyorlanish: {prep} s.\n🎙 Javob berish vaqti (⭐ — rasmiy). Boshqa son kerak bo'lsa, "
                         f"yozib yuboring (10–600):", reply_markup=_seconds_kb("q_ans", ANSWER_OPTIONS, answer))


@router.callback_query(NewQuestion.prep, F.data.startswith("q_prep:"))
async def new_question_prep(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_reply_markup(reply_markup=None)
    await _set_prep(callback.message, state, int(callback.data.split(":")[1]))
    await callback.answer()


@router.message(NewQuestion.prep)
async def new_question_prep_typed(message: Message, state: FSMContext):
    data = await state.get_data()
    value = None if data.get("times_menu") else _parse_seconds(message.text, PREP_RANGE)
    if value is None:
        return await message.answer("Yuqoridagi tugmalardan tanlang" + ("" if data.get("times_menu") else
                                    " yoki 0 dan 300 gacha son yozing") + ". Bekor qilish: /cancel")
    await _set_prep(message, state, value)


@router.callback_query(NewQuestion.answer, F.data.startswith("q_ans:"))
async def new_question_answer(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_reply_markup(reply_markup=None)
    await _save_new_question(callback.message, state, int(callback.data.split(":")[1]))
    await callback.answer()


@router.message(NewQuestion.answer)
async def new_question_answer_typed(message: Message, state: FSMContext):
    value = _parse_seconds(message.text, ANSWER_RANGE)
    if value is None:
        return await message.answer("Tugmalardan tanlang yoki 10 dan 600 gacha son yozing. Bekor qilish: /cancel")
    await _save_new_question(message, state, value)


async def _save_new_question(message: Message, state: FSMContext, answer: int):
    data = await state.get_data()
    if "text" not in data or "prep" not in data:
        await state.clear()
        return await message.answer("Bu amal eskirgan. Testni ochib, «➕ Savol qo'shish» ni qaytadan bosing.")
    exam_id = data["exam_id"]
    if not await db.get_exam(exam_id):
        await state.clear()
        return await message.answer("Test topilmadi (o'chirilgan bo'lishi mumkin).")
    await db.add_question(exam_id, data["text"], data.get("photo"), data["prep"], answer)
    await state.update_data(last_times=[data["prep"], answer])
    n = len(await db.get_questions(exam_id))
    count = part_info("speaking", data.get("part")).get("count")
    saved = f"✅ {n}-savol saqlandi (⏳ {data['prep']} s, 🎙 {answer} s).\n\n"
    if count and n < count:  # rasmiy formatdagi savollar soni to'lguncha keyingisi darhol so'raladi
        return await _ask_question_text(message, state, exam_id, intro=saved)
    await state.clear()
    done = f"🎉 Barcha {count} ta savol tayyor — o'quvchilar testni ko'ra oladi." if count and n == count else ""
    await message.answer(saved + done if done else saved.strip())
    text, kb = await _exam_view(exam_id)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


# ---------- Bitta savolni tahrirlash ----------

async def _question_view(question_id: int):
    q = await db.get_question(question_id)
    if not q:
        return None, None
    questions = await db.get_questions(q["exam_id"])
    pos = next((i for i, x in enumerate(questions, 1) if x["id"] == question_id), 1)
    lines = [
        f"✏️ <b>{pos}-savol</b> (jami {len(questions)} ta)\n",
        e(q["text"]),
        f"\n🖼 Rasm: {'bor' if q['photo_file_id'] else 'yo‘q'}",
        f"⏳ Tayyorlanish: <b>{q['prep_sec']} s</b>",
        f"🎙 Javob berish: <b>{q['answer_sec']} s</b>",
    ]
    qid = question_id
    rows = [
        [InlineKeyboardButton(text="✏️ Matnni o'zgartirish", callback_data=f"qe_t:{qid}")],
        [InlineKeyboardButton(text="⏳ Tayyorlanish vaqti", callback_data=f"qe_prep:{qid}"),
         InlineKeyboardButton(text="🎙 Javob vaqti", callback_data=f"qe_ans:{qid}")],
    ]
    photo = [InlineKeyboardButton(text="🖼 Rasmni almashtirish" if q["photo_file_id"] else "🖼 Rasm qo'shish",
                                  callback_data=f"qe_p:{qid}")]
    if q["photo_file_id"]:
        photo.append(InlineKeyboardButton(text="🗑 Rasmni olib tashlash", callback_data=f"qe_px:{qid}"))
    rows.append(photo)
    move = []
    if pos > 1:
        move.append(InlineKeyboardButton(text="⬆️ Yuqoriga", callback_data=f"qe_up:{qid}"))
    if pos < len(questions):
        move.append(InlineKeyboardButton(text="⬇️ Pastga", callback_data=f"qe_dn:{qid}"))
    if move:
        rows.append(move)
    rows.append([InlineKeyboardButton(text="🗑 Savolni o'chirish", callback_data=f"q_delask:{qid}")])
    rows.append([InlineKeyboardButton(text="⬅️ Testga qaytish", callback_data=f"exam:{q['exam_id']}")])
    return _join_limited(lines), InlineKeyboardMarkup(inline_keyboard=rows)


async def _show_question(callback: CallbackQuery, question_id: int, note: str | None = None):
    text, kb = await _question_view(question_id)
    if not text:
        return await callback.answer("Savol topilmadi (o'chirilgan bo'lishi mumkin).", show_alert=True)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer(note)


async def _send_question(message: Message, question_id: int, note: str):
    text, kb = await _question_view(question_id)
    await message.answer(note)
    if text:
        await message.answer(text, parse_mode="HTML", reply_markup=kb)


def _qid(callback: CallbackQuery) -> int:
    return int(callback.data.split(":")[1])


@router.callback_query(F.data.startswith("q_edit:"))
async def edit_question(callback: CallbackQuery, state: FSMContext):
    if not _admin_only(callback.from_user.id):
        return await callback.answer()
    await state.clear()
    await _show_question(callback, _qid(callback))


@router.callback_query(F.data.startswith("qe_up:") | F.data.startswith("qe_dn:"))
async def move_question(callback: CallbackQuery):
    if not _admin_only(callback.from_user.id):
        return await callback.answer()
    moved = await db.move_question(_qid(callback), -1 if callback.data.startswith("qe_up:") else 1)
    await _show_question(callback, _qid(callback), None if moved else "Bu savolni surib bo'lmaydi")


@router.callback_query(F.data.startswith("qe_t:"))
async def edit_question_text_ask(callback: CallbackQuery, state: FSMContext):
    if not _admin_only(callback.from_user.id):
        return await callback.answer()
    q = await db.get_question(_qid(callback))
    if not q:
        return await callback.answer("Savol topilmadi.", show_alert=True)
    await state.clear()
    await state.set_state(EditQuestion.text)
    await state.update_data(qid=q["id"])
    await callback.message.answer(f"Hozirgi matn:\n<code>{e(q['text'])}</code>\n\nYangi matnni yozing. Bekor qilish: /cancel",
                                  parse_mode="HTML")
    await callback.answer()


@router.message(EditQuestion.text)
async def edit_question_text_save(message: Message, state: FSMContext):
    if not _admin_only(message.from_user.id):
        return
    text = (message.text or "").strip()
    if not text or text.startswith("/"):
        return await message.answer("Savolni matn ko'rinishida yozing. Bekor qilish: /cancel")
    qid = (await state.get_data()).get("qid")
    await state.clear()
    if not qid or not await db.get_question(qid):
        return await message.answer("Savol topilmadi (o'chirilgan bo'lishi mumkin).")
    await db.update_question(qid, text=text[:2500])
    await _send_question(message, qid, "✅ Matn saqlandi.")


@router.callback_query(F.data.startswith("qe_p:"))
async def edit_question_photo_ask(callback: CallbackQuery, state: FSMContext):
    if not _admin_only(callback.from_user.id):
        return await callback.answer()
    await state.clear()
    await state.set_state(EditQuestion.photo)
    await state.update_data(qid=_qid(callback))
    await callback.message.answer("Yangi rasmni yuboring. Bekor qilish: /cancel")
    await callback.answer()


@router.message(EditQuestion.photo, F.photo)
async def edit_question_photo_save(message: Message, state: FSMContext):
    qid = (await state.get_data()).get("qid")
    await state.clear()
    if not qid or not await db.get_question(qid):
        return await message.answer("Savol topilmadi (o'chirilgan bo'lishi mumkin).")
    await db.update_question(qid, photo_file_id=message.photo[-1].file_id)
    await _send_question(message, qid, "✅ Rasm saqlandi.")


@router.message(EditQuestion.photo)
async def edit_question_photo_wrong(message: Message):
    await message.answer("Rasm yuboring. Bekor qilish: /cancel")


@router.callback_query(F.data.startswith("qe_px:"))
async def edit_question_photo_remove(callback: CallbackQuery):
    if not _admin_only(callback.from_user.id):
        return await callback.answer()
    if await db.get_question(_qid(callback)):
        await db.update_question(_qid(callback), photo_file_id=None)
    await _show_question(callback, _qid(callback), "Rasm olib tashlandi")


@router.callback_query(F.data.startswith("qe_prep:") | F.data.startswith("qe_ans:"))
async def edit_question_time_ask(callback: CallbackQuery, state: FSMContext):
    if not _admin_only(callback.from_user.id):
        return await callback.answer()
    q = await db.get_question(_qid(callback))
    if not q:
        return await callback.answer("Savol topilmadi.", show_alert=True)
    exam = await db.get_exam(q["exam_id"])
    pos = next((i for i, x in enumerate(await db.get_questions(q["exam_id"]), 1) if x["id"] == q["id"]), 1)
    prep, answer = speaking_times(exam.get("part") if exam else None, pos)
    is_prep = callback.data.startswith("qe_prep:")
    await state.clear()
    await state.set_state(EditQuestion.prep if is_prep else EditQuestion.answer)
    await state.update_data(qid=q["id"])
    back = [[InlineKeyboardButton(text="⬅️ Orqaga", callback_data=f"q_edit:{q['id']}")]]
    kb = (_seconds_kb(f"qs_p:{q['id']}", PREP_OPTIONS, prep, back) if is_prep
          else _seconds_kb(f"qs_a:{q['id']}", ANSWER_OPTIONS, answer, back))
    label = (f"⏳ Tayyorlanish vaqti (hozir {q['prep_sec']} s)" if is_prep
             else f"🎙 Javob berish vaqti (hozir {q['answer_sec']} s)")
    bounds = "0–300" if is_prep else "10–600"
    await callback.message.edit_text(f"{label}.\n⭐ — rasmiy. Boshqa son kerak bo'lsa, yozib yuboring ({bounds}):",
                                     reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("qs_p:") | F.data.startswith("qs_a:"))
async def edit_question_time_save(callback: CallbackQuery, state: FSMContext):
    if not _admin_only(callback.from_user.id):
        return await callback.answer()
    _, qid, value = callback.data.split(":")
    await state.clear()
    if not await db.get_question(int(qid)):
        return await callback.answer("Savol topilmadi.", show_alert=True)
    field = "prep_sec" if callback.data.startswith("qs_p:") else "answer_sec"
    await db.update_question(int(qid), **{field: int(value)})
    await _show_question(callback, int(qid), "Saqlandi")


@router.message(EditQuestion.prep)
@router.message(EditQuestion.answer)
async def edit_question_time_typed(message: Message, state: FSMContext):
    is_prep = await state.get_state() == EditQuestion.prep.state
    value = _parse_seconds(message.text, PREP_RANGE if is_prep else ANSWER_RANGE)
    if value is None:
        return await message.answer("Tugmalardan tanlang yoki " + ("0 dan 300" if is_prep else "10 dan 600")
                                    + " gacha son yozing. Bekor qilish: /cancel")
    qid = (await state.get_data()).get("qid")
    await state.clear()
    if not qid or not await db.get_question(qid):
        return await message.answer("Savol topilmadi (o'chirilgan bo'lishi mumkin).")
    await db.update_question(qid, **{"prep_sec" if is_prep else "answer_sec": value})
    await _send_question(message, qid, "✅ Vaqt saqlandi.")


# ---------- Hamma savollar uchun bir xil vaqt ----------

@router.callback_query(F.data.startswith("qall:"))
async def all_times_prep(callback: CallbackQuery):
    if not _admin_only(callback.from_user.id):
        return await callback.answer()
    exam_id = _qid(callback)
    exam = await db.get_exam(exam_id)
    if not exam:
        return await callback.answer("Test topilmadi.", show_alert=True)
    prep, _ = speaking_times(exam.get("part"), 1)
    extra = [[InlineKeyboardButton(text="O'zgartirmaslik ➡️", callback_data=f"qap:{exam_id}:-")],
             [InlineKeyboardButton(text="⬅️ Orqaga", callback_data=f"exam:{exam_id}")]]
    await callback.message.edit_text("⏱ Hamma savollar uchun bir xil vaqt.\n\n1/2. ⏳ Tayyorlanish vaqti (⭐ — rasmiy):",
                                     reply_markup=_seconds_kb(f"qap:{exam_id}", PREP_OPTIONS, prep, extra))
    await callback.answer()


@router.callback_query(F.data.startswith("qap:"))
async def all_times_answer(callback: CallbackQuery):
    if not _admin_only(callback.from_user.id):
        return await callback.answer()
    _, exam_id, prep = callback.data.split(":")
    exam = await db.get_exam(int(exam_id))
    if not exam:
        return await callback.answer("Test topilmadi.", show_alert=True)
    _, answer = speaking_times(exam.get("part"), 2)
    extra = [[InlineKeyboardButton(text="O'zgartirmaslik", callback_data=f"qaa:{exam_id}:{prep}:-")],
             [InlineKeyboardButton(text="⬅️ Orqaga", callback_data=f"exam:{exam_id}")]]
    chosen = "o'zgarmaydi" if prep == "-" else f"{prep} s"
    await callback.message.edit_text(f"⏱ Hamma savollar uchun bir xil vaqt.\n⏳ Tayyorlanish: {chosen}\n\n"
                                     "2/2. 🎙 Javob berish vaqti (⭐ — rasmiy):",
                                     reply_markup=_seconds_kb(f"qaa:{exam_id}:{prep}", ANSWER_OPTIONS, answer, extra))
    await callback.answer()


@router.callback_query(F.data.startswith("qaa:"))
async def all_times_save(callback: CallbackQuery):
    if not _admin_only(callback.from_user.id):
        return await callback.answer()
    _, exam_id, prep, answer = callback.data.split(":")
    exam_id = int(exam_id)
    if not await db.get_exam(exam_id):
        return await callback.answer("Test topilmadi.", show_alert=True)
    await db.set_exam_times(exam_id, None if prep == "-" else int(prep), None if answer == "-" else int(answer))
    text, kb = await _exam_view(exam_id)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer("Vaqtlar saqlandi")


# ---------- Savolni o'chirish ----------

@router.callback_query(F.data.startswith("q_delask:"))
async def delete_question_ask(callback: CallbackQuery):
    if not _admin_only(callback.from_user.id):
        return await callback.answer()
    q = await db.get_question(_qid(callback))
    if not q:
        return await callback.answer("Savol topilmadi.", show_alert=True)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Ha, o'chirish", callback_data=f"q_del:{q['id']}"),
        InlineKeyboardButton(text="❌ Yo'q", callback_data=f"q_edit:{q['id']}"),
    ]])
    await callback.message.edit_text(f"Bu savol o'chirilsinmi?\n\n<i>{e(_preview(q['text'], 200))}</i>",
                                     parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("q_del:"))
async def delete_question(callback: CallbackQuery):
    if not _admin_only(callback.from_user.id):
        return await callback.answer()
    q = await db.get_question(int(callback.data.split(":")[1]))
    if not q:
        return await callback.answer("Savol topilmadi.")
    await db.delete_question(q["id"])
    text, kb = await _exam_view(q["exam_id"])
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer("O'chirildi")
