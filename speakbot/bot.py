"""Telegram bot: o'quvchi uchun Speaking va Writing bo'limlari, admin uchun panel.

O'quvchi:  Speaking -> tur (Part 1.1 / 1.2 / 2 / 3) -> imtihon -> mini app
           Writing  -> tur (Task 1.1 / 1.2 / 2) -> mavzu -> matnni xabar qilib yuboradi
Admin:     yangi imtihon -> Speaking yoki Writing -> tur -> nom -> savollar / topshiriq matni
"""
import html
import json
import logging
import time

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
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
from config import ADMIN_IDS, EXAM_LANGUAGE, WEBAPP_URL, is_admin
from languages import LANGUAGES
from parts import SPEAKING_PARTS, WRITING_PARTS, part_info, parts_for

LANG = LANGUAGES[EXAM_LANGUAGE]
log = logging.getLogger("bot")

router = Router()
router.message.filter(F.chat.type == "private")

SPEAKING_NAME = LANG["speaking_name"]   # turkcha: "Konuşma"
WRITING_NAME = LANG["writing_name"]     # turkcha: "Yazma"

BTN_SPEAKING = f"🎙 {SPEAKING_NAME}"
BTN_WRITING = f"✍️ {WRITING_NAME}"
BTN_ADMIN = "⚙️ Admin panel"
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


class WritingAnswer(StatesGroup):
    text = State()


WRITING_TASK_HINT = {
    "1": (
        "Rasmiy formatda topshiriq quyidagilardan iborat bo'ladi:\n"
        "1) vaziyat (masalan: <i>Siz sayohat klubining a'zosisiz. Klubdan quyidagi xatni oldingiz:</i>);\n"
        "2) 50–80 so'zli xat matni - unda o'quvchi javob berishi kerak bo'lgan 3 ta band bo'lsin;\n"
        "3) 1-xat ko'rsatmasi: do'stingizga yozing (~50 so'z);\n"
        "4) 2-xat ko'rsatmasi: menejerga / rahbariyatga yozing (120–150 so'z)."
    ),
    "2": (
        "Rasmiy formatda: janr (blog posti, forum posti yoki maqola), muhokama qilinadigan fikr "
        "yoki savol va talab (180–200 so'z). Masalan: <i>Siz onlayn ishlashning ijobiy va salbiy "
        "jihatlari haqida blog posti yozmoqdasiz. O'z fikringizni bayon qiling va misollar keltiring. "
        "180–200 so'z yozing.</i>"
    ),
}


def e(value) -> str:
    return html.escape(str(value or ""), quote=False)


def _pkey(part: str | None) -> str:
    """callback_data uchun: turi belgilanmagan eski imtihonlar 'x' bo'ladi."""
    return part or "x"


def _part_from_key(key: str) -> str | None:
    return None if key == "x" else key


def main_keyboard(user_id: int) -> ReplyKeyboardMarkup:
    rows = [[KeyboardButton(text=BTN_SPEAKING), KeyboardButton(text=BTN_WRITING)]]
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


async def _parts_keyboard(kind: str) -> InlineKeyboardMarkup | None:
    counts = await db.active_part_counts(kind)
    if not counts:
        return None
    prefix = "sp" if kind == "speaking" else "wr"
    rows = []
    for key, info in parts_for(kind).items():
        if key in counts:
            rows.append([InlineKeyboardButton(text=f"{info['name']} ({counts[key]})", callback_data=f"{prefix}:{key}")])
    if None in counts:
        rows.append([InlineKeyboardButton(text=f"Boshqa ({counts[None]})", callback_data=f"{prefix}:x")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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
async def speaking_menu_cb(callback: CallbackQuery):
    await _show_parts(callback.message, "speaking", edit=True)
    await callback.answer()


@router.callback_query(F.data.startswith("sp:"))
async def speaking_part(callback: CallbackQuery):
    part = _part_from_key(callback.data.split(":", 1)[1])
    info = part_info("speaking", part)
    exams = await db.list_active_exams("speaking", part)
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
async def writing_part(callback: CallbackQuery):
    part = _part_from_key(callback.data.split(":", 1)[1])
    info = part_info("writing", part)
    exams = await db.list_active_exams("writing", part)
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


@router.callback_query(F.data.startswith("wt:"))
async def writing_topic(callback: CallbackQuery, state: FSMContext):
    exam = await db.get_exam(int(callback.data.split(":")[1]))
    questions = await db.get_questions(exam["id"]) if exam else []
    if not exam or not exam["is_active"] or not questions:
        return await callback.answer("Bu mavzu hozir yopiq.", show_alert=True)
    info = part_info("writing", exam["part"])
    task = questions[0]
    await state.set_state(WritingAnswer.text)
    await state.update_data(exam_id=exam["id"], step=0, texts={})
    steps = "\n".join(f"• {e(c['name'])} — {e(c['words'])}" for c in info["components"])
    text = (
        f"✍️ <b>{e(exam['title'])}</b>\n<i>{e(info['name'])}</i>\n\n"
        f"{e(task['text'])}\n\n"
        f"📏 Yozish kerak:\n{steps}"
    )
    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Bekor qilish", callback_data="wr_cancel")]])
    if task["photo_file_id"]:
        await callback.message.answer_photo(task["photo_file_id"])
    await callback.message.answer(text, parse_mode="HTML")
    await callback.message.answer(_ask_component_text(info, 0), parse_mode="HTML", reply_markup=cancel_kb)
    await callback.answer()


def _ask_component_text(info: dict, step: int) -> str:
    c = info["components"][step]
    total = len(info["components"])
    num = f"({step + 1}/{total}) " if total > 1 else ""
    return (
        f"✏️ {num}<b>{e(c['name'])}</b> — {e(c['words'])}.\n"
        "Matnni <b>bitta xabar</b> qilib yuboring. Bekor qilish: /cancel"
    )


@router.message(WritingAnswer.text, F.text & ~F.text.startswith("/"))
async def writing_answer(message: Message, state: FSMContext):
    if message.text in {BTN_SPEAKING, BTN_WRITING, BTN_ADMIN} | OLD_SPEAKING_BUTTONS | OLD_WRITING_BUTTONS:
        await state.clear()
        return await message.answer(f"{WRITING_NAME} bekor qilindi. Bo'limni qaytadan tanlang.",
                                    reply_markup=main_keyboard(message.from_user.id))

    data = await state.get_data()
    exam = await db.get_exam(data.get("exam_id", 0))
    questions = await db.get_questions(exam["id"]) if exam else []
    if not exam or not questions:
        await state.clear()
        return await message.answer("Mavzu topilmadi. Qaytadan tanlang.")

    text = message.text.strip()
    if len(text.split()) < 5:
        return await message.answer("Matn juda qisqa. To'liq javobingizni bitta xabar qilib yuboring.")

    info = part_info("writing", exam["part"])
    comps = info["components"]
    step = data.get("step", 0)
    texts = dict(data.get("texts") or {})
    texts[comps[step]["key"]] = text

    if step + 1 < len(comps):  # 1-qism: endi 2-xatni so'raymiz
        await state.update_data(step=step + 1, texts=texts)
        return await message.answer("✅ Qabul qilindi.\n\n" + _ask_component_text(info, step + 1), parse_mode="HTML")

    await state.clear()  # bir ish ikki marta baholanmasin
    joined = "\n\n".join(f"[{c['name']}]\n{texts.get(c['key'], '')}" for c in comps)
    words = sum(len(t.split()) for t in texts.values())
    submission_id = await db.create_writing_submission(message.from_user.id, exam["id"], joined, words)
    wait = await message.answer("⏳ Ishingiz rasmiy mezonlar bo'yicha tekshirilmoqda... (10–40 soniya)")

    try:
        result = await grader.grade_writing(questions[0]["text"], texts, exam["part"])
    except Exception as err:
        log.exception("Writing baholash xatosi (submission %s)", submission_id)
        if getattr(err, "status", None) == 429:
            msg = "Hozir tekshirish navbati band. 1–2 daqiqadan keyin mavzuni qayta tanlab yuboring."
        else:
            msg = "Tekshirishda xatolik bo'ldi. Birozdan keyin mavzuni qayta tanlab yuboring."
        await wait.edit_text(msg)
        return

    await db.finish_writing_submission(submission_id, result["total"], result["level"], result["raw"], result)
    try:
        await wait.delete()
    except Exception:
        pass
    await message.answer(grader.result_message(exam["title"], info["name"], result), parse_mode="HTML")

    for admin_id in ADMIN_IDS:
        try:
            await message.bot.send_message(
                admin_id,
                f"📥 ✍️ {e(message.from_user.full_name)} (<code>{message.from_user.id}</code>) — "
                f"{e(exam['title'])}: <b>{float(result['raw']):g}/{result['max_raw']}</b> ({e(result['level'])})",
                parse_mode="HTML",
            )
        except Exception:
            pass


@router.message(WritingAnswer.text)
async def writing_answer_wrong(message: Message):
    await message.answer("Javobni <b>matn</b> ko'rinishida yuboring. Bekor qilish: /cancel", parse_mode="HTML")


# ======================================================================
# ADMIN: imtihonlar ro'yxati
# ======================================================================

def _admin_only(user_id: int) -> bool:
    return is_admin(user_id)


async def _admin_panel_view():
    exams = await db.list_exams()
    lines = ["⚙️ <b>Admin panel</b>\n"]
    rows = []
    if not exams:
        lines.append("Hali imtihon yo'q. Birinchisini yarating.")
    for ex in exams:
        kind = ex.get("kind") or "speaking"
        status = "🟢" if ex["is_active"] else "⚪️"
        part = part_info(kind, ex.get("part"))["name"]
        lines.append(f"{status} {KIND_ICON[kind]} <b>{e(ex['title'])}</b> — {e(part)}")
        rows.append(
            [InlineKeyboardButton(text=f"{status} {KIND_ICON[kind]} {ex['title']}"[:60], callback_data=f"exam:{ex['id']}")]
        )
    rows.append([InlineKeyboardButton(text="➕ Yangi imtihon / mavzu", callback_data="exam_new")])
    rows.append([InlineKeyboardButton(text="📊 Oxirgi natijalar", callback_data="results")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


async def _exam_view(exam_id: int):
    exam = await db.get_exam(exam_id)
    if not exam:
        return "Imtihon topilmadi.", InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⬅️ Orqaga", callback_data="admin")]]
        )
    kind = exam.get("kind") or "speaking"
    info = part_info(kind, exam.get("part"))
    questions = await db.get_questions(exam_id)
    status = "🟢 Ochiq (o'quvchilarga ko'rinadi)" if exam["is_active"] else "⚪️ Yopiq"
    lines = [f"{KIND_ICON[kind]} <b>{e(exam['title'])}</b>\n{KIND_NAME[kind]} • {e(info['name'])}\n{status}\n"]
    rows = []

    if kind == "writing":
        if questions:
            photo = "🖼 " if questions[0]["photo_file_id"] else ""
            lines.append(f"<b>Topshiriq:</b>\n{photo}{e(questions[0]['text'])}")
        else:
            lines.append("Topshiriq matni yo'q. «✏️ Topshiriq matni» tugmasini bosing.")
        rows.append([InlineKeyboardButton(text="✏️ Topshiriq matni", callback_data=f"wtask:{exam_id}")])
    else:
        if info.get("questions_hint"):
            lines.append(f"💡 {e(info['questions_hint'])}\n")
        if not questions:
            lines.append("Savollar yo'q. «➕ Savol qo'shish» tugmasini bosing.")
        for i, q in enumerate(questions, 1):
            photo = " 🖼" if q["photo_file_id"] else ""
            lines.append(f"<b>{i}.</b>{photo} {e(q['text'])}\n    ⏳ {q['prep_sec']} s, 🎙 {q['answer_sec']} s")
        rows.append([InlineKeyboardButton(text="➕ Savol qo'shish", callback_data=f"q_new:{exam_id}")])
        for i, q in enumerate(questions, 1):
            rows.append([InlineKeyboardButton(text=f"🗑 {i}-savolni o'chirish", callback_data=f"q_del:{q['id']}")])

    toggle = "⚪️ Yopish" if exam["is_active"] else "🟢 O'quvchilarga ochish"
    rows.append([InlineKeyboardButton(text=toggle, callback_data=f"exam_toggle:{exam_id}")])
    rows.append([InlineKeyboardButton(text="🗑 O'chirish", callback_data=f"exam_del_ask:{exam_id}")])
    rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="admin")])
    return "\n".join(lines)[:4000], InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(F.text == BTN_ADMIN)
@router.message(Command("admin"))
async def admin_panel(message: Message, state: FSMContext):
    if not _admin_only(message.from_user.id):
        return
    await state.clear()
    text, kb = await _admin_panel_view()
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data == "admin")
async def admin_panel_cb(callback: CallbackQuery, state: FSMContext):
    if not _admin_only(callback.from_user.id):
        return await callback.answer()
    await state.clear()
    text, kb = await _admin_panel_view()
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("exam:"))
async def open_exam(callback: CallbackQuery):
    if not _admin_only(callback.from_user.id):
        return await callback.answer()
    text, kb = await _exam_view(int(callback.data.split(":")[1]))
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


# ---------- Yangi imtihon: tur -> qism -> nom ----------

@router.callback_query(F.data == "exam_new")
async def new_exam(callback: CallbackQuery, state: FSMContext):
    if not _admin_only(callback.from_user.id):
        return await callback.answer()
    await state.clear()
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=f"🎙 {SPEAKING_NAME}", callback_data="nk:speaking"),
                InlineKeyboardButton(text=f"✍️ {WRITING_NAME}", callback_data="nk:writing"),
            ],
            [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="admin")],
        ]
    )
    await callback.message.edit_text("1/3. Qaysi bo'lim uchun?", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("nk:"))
async def new_exam_kind(callback: CallbackQuery, state: FSMContext):
    if not _admin_only(callback.from_user.id):
        return await callback.answer()
    kind = callback.data.split(":")[1]
    await state.update_data(kind=kind)
    rows = [[InlineKeyboardButton(text=info["name"], callback_data=f"np:{key}")] for key, info in parts_for(kind).items()]
    rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="exam_new")])
    await callback.message.edit_text(
        f"2/3. {KIND_ICON[kind]} {KIND_NAME[kind]} — qaysi turdagi?", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("np:"))
async def new_exam_part(callback: CallbackQuery, state: FSMContext):
    if not _admin_only(callback.from_user.id):
        return await callback.answer()
    data = await state.get_data()
    kind = data.get("kind", "speaking")
    part = callback.data.split(":", 1)[1]
    await state.update_data(part=part)
    await state.set_state(NewExam.title)
    what = "mavzu" if kind == "writing" else "imtihon"
    await callback.message.edit_text(
        f"3/3. {part_info(kind, part)['name']}\n\n{what.capitalize()} nomini yozing.\n"
        f"Masalan: <i>{'Shahar haqida xat' if kind == 'writing' else 'Mock #1'}</i>",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(NewExam.title)
async def new_exam_title(message: Message, state: FSMContext):
    title = (message.text or "").strip()
    if not title:
        return await message.answer("Nomni matn ko'rinishida yozing.")
    data = await state.get_data()
    kind = data.get("kind", "speaking")
    exam_id = await db.create_exam(title[:100], kind, data.get("part"))
    await state.clear()

    if kind == "writing":
        await state.set_state(WritingTask.text)
        await state.update_data(exam_id=exam_id)
        return await message.answer(
            "✅ Mavzu yaratildi.\n\nEndi <b>topshiriq matnini</b> bitta xabar qilib yuboring "
            f"({LANG['name_uz']}da).\n\n{WRITING_TASK_HINT.get(data.get('part'), '')}",
            parse_mode="HTML",
        )

    text, kb = await _exam_view(exam_id)
    await message.answer("✅ Imtihon yaratildi. Endi savollar qo'shing.")
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


# ---------- Writing topshiriq matni ----------

@router.callback_query(F.data.startswith("wtask:"))
async def writing_task_edit(callback: CallbackQuery, state: FSMContext):
    if not _admin_only(callback.from_user.id):
        return await callback.answer()
    await state.set_state(WritingTask.text)
    await state.update_data(exam_id=int(callback.data.split(":")[1]))
    await callback.message.answer(
        "Yangi topshiriq matnini yuboring (eski matn almashtiriladi). "
        "Rasm kerak bo'lsa, rasmni izoh (caption) bilan yuboring."
    )
    await callback.answer()


@router.message(WritingTask.text)
async def writing_task_save(message: Message, state: FSMContext):
    text = (message.text or message.caption or "").strip()
    if not text:
        return await message.answer("Topshiriq matnini yozing (rasm bo'lsa - izoh sifatida).")
    photo = message.photo[-1].file_id if message.photo else None
    data = await state.get_data()
    await db.set_writing_task(data["exam_id"], text[:3500], photo)
    await state.clear()
    view, kb = await _exam_view(data["exam_id"])
    await message.answer("✅ Topshiriq saqlandi. Tayyor bo'lsa, «🟢 O'quvchilarga ochish» tugmasini bosing.")
    await message.answer(view, parse_mode="HTML", reply_markup=kb)


# ---------- Ochish / yopish / o'chirish / natijalar ----------

@router.callback_query(F.data.startswith("exam_toggle:"))
async def toggle_exam(callback: CallbackQuery):
    if not _admin_only(callback.from_user.id):
        return await callback.answer()
    exam_id = int(callback.data.split(":")[1])
    exam = await db.get_exam(exam_id)
    if not exam["is_active"] and not await db.get_questions(exam_id):
        what = "topshiriq matnini" if exam.get("kind") == "writing" else "kamida bitta savol"
        return await callback.answer(f"Avval {what} qo'shing.", show_alert=True)
    await db.set_exam_active(exam_id, not exam["is_active"])
    text, kb = await _exam_view(exam_id)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer("Saqlandi")


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
    text, kb = await _admin_panel_view()
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer("O'chirildi")


@router.callback_query(F.data == "results")
async def show_results(callback: CallbackQuery):
    if not _admin_only(callback.from_user.id):
        return await callback.answer()
    rows = await db.recent_results(20)
    if not rows:
        text = "Hali hech kim topshirmagan."
    else:
        lines = ["📊 <b>Oxirgi 20 ta natija</b>\n"]
        for r in rows:
            try:
                res = json.loads(r["result_json"] or "{}")
                score = f"{float(res['raw']):g}/{res['max_raw']}"
            except (ValueError, KeyError, TypeError):
                score = f"{r['score']}/75"
            lines.append(
                f"{KIND_ICON.get(r['kind'], '')} {e(r['full_name'] or '?')} — "
                f"<b>{score}</b> ({e(r['level'])}), {e(r['title'] or '?')}"
            )
        text = "\n".join(lines)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Orqaga", callback_data="admin")]])
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


# ======================================================================
# ADMIN: speaking savoli qo'shish
# ======================================================================

SKIP_PHOTO = InlineKeyboardMarkup(
    inline_keyboard=[[InlineKeyboardButton(text="Rasmsiz davom etish ➡️", callback_data="q_nophoto")]]
)


def _seconds_kb(prefix: str, options: list[int], recommended: int) -> InlineKeyboardMarkup:
    opts = sorted(set(options + [recommended]))
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=f"{'⭐ ' if s == recommended else ''}{s} s", callback_data=f"{prefix}:{s}")
                for s in opts
            ]
        ]
    )


@router.callback_query(F.data.startswith("q_new:"))
async def new_question(callback: CallbackQuery, state: FSMContext):
    if not _admin_only(callback.from_user.id):
        return await callback.answer()
    exam_id = int(callback.data.split(":")[1])
    exam = await db.get_exam(exam_id)
    await state.set_state(NewQuestion.text)
    await state.update_data(exam_id=exam_id, part=exam.get("part") if exam else None)
    await callback.message.answer(
        f"1/4. Savol matnini yozing ({LANG['name_uz']}da).\n"
        f"Masalan: <i>{e(LANG['question_example'])}</i>",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(NewQuestion.text)
async def new_question_text(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if not text:
        return await message.answer("Savolni matn ko'rinishida yozing.")
    await state.update_data(text=text[:2500])
    await state.set_state(NewQuestion.photo)
    await message.answer("2/4. Savolga rasm kerak bo'lsa (masalan, Part 1.2 uchun), rasmni yuboring.", reply_markup=SKIP_PHOTO)


async def _ask_prep(message: Message, state: FSMContext):
    data = await state.get_data()
    info = part_info("speaking", data.get("part"))
    await state.set_state(NewQuestion.prep)
    await message.answer(
        "3/4. Tayyorlanish vaqti (⭐ — shu tur uchun tavsiya):",
        reply_markup=_seconds_kb("q_prep", [0, 15, 30, 60], info["prep"]),
    )


@router.message(NewQuestion.photo, F.photo)
async def new_question_photo(message: Message, state: FSMContext):
    await state.update_data(photo=message.photo[-1].file_id)
    await _ask_prep(message, state)


@router.callback_query(NewQuestion.photo, F.data == "q_nophoto")
async def new_question_nophoto(callback: CallbackQuery, state: FSMContext):
    await state.update_data(photo=None)
    await _ask_prep(callback.message, state)
    await callback.answer()


@router.message(NewQuestion.photo)
async def new_question_photo_wrong(message: Message):
    await message.answer("Rasm yuboring yoki «Rasmsiz davom etish» tugmasini bosing.", reply_markup=SKIP_PHOTO)


@router.callback_query(NewQuestion.prep, F.data.startswith("q_prep:"))
async def new_question_prep(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    info = part_info("speaking", data.get("part"))
    await state.update_data(prep=int(callback.data.split(":")[1]))
    await state.set_state(NewQuestion.answer)
    await callback.message.edit_text(
        "4/4. Javob berish vaqti (⭐ — shu tur uchun tavsiya):",
        reply_markup=_seconds_kb("q_ans", [30, 60, 90, 120], info["answer"]),
    )
    await callback.answer()


@router.callback_query(NewQuestion.answer, F.data.startswith("q_ans:"))
async def new_question_answer(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await db.add_question(data["exam_id"], data["text"], data.get("photo"), data["prep"], int(callback.data.split(":")[1]))
    await state.clear()
    await callback.message.edit_text("✅ Savol qo'shildi.")
    text, kb = await _exam_view(data["exam_id"])
    await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)
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