"""👑 Boss paneli (faqat ADMIN_IDS dagi asosiy adminlar): tushum, adminlar va ularning huquqlari,
tarif/narx sozlamalari, tizim holati. Adminlar ro'yxati va qo'shish/o'chirish - premium.py da."""
import asyncio
import html
import re
import time
from datetime import datetime, timedelta

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

import db
import plans
import roles
from config import ADMIN_IDS, WEBAPP_URL, is_owner

router = Router()
LINE = "━━━━━━━━━━━━━━━━"


def e(value) -> str:
    return html.escape(str(value or ""), quote=False)


def _b(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _amount(row: dict) -> tuple[int, bool]:
    """(summa, aniqmi): to'lov summasi yozilgan bo'lsa (chek tasdiqlanganda - tarif narxi) - o'sha,
    aks holda tarif narxi × muddat bo'yicha taxmin (masalan, 365 kunlik Pro ≈ 12 oylik narx)."""
    digits = re.sub(r"\D", "", row.get("amount") or "")
    if len(digits) >= 4:
        return int(digits), True
    price = plans.price_for(row.get("plan") or "pro")
    return round(price * max(1, row.get("days") or 30) / plans.PERIOD_DAYS), False


def _date(stamp: str | None) -> str:
    """UTC "YYYY-MM-DD HH:MM:SS" -> Toshkent vaqti "15.10.2026"."""
    if not stamp:
        return "—"
    try:
        return (datetime.strptime(stamp[:19], "%Y-%m-%d %H:%M:%S") + timedelta(hours=5)).strftime("%d.%m.%Y")
    except ValueError:
        return stamp[:10]


def person(name, username, uid) -> str:
    """«Ali Valiyev (@ali)» yoki ism bo'lmasa - ID."""
    label = e(name or (f"ID {uid}" if uid else "?"))
    return f"{label} (@{e(username)})" if username else label


async def _money(days: int = 30) -> dict:
    rows = await db.grants_since(days)
    total = exact_sum = approx_sum = 0
    by_plan: dict[str, list[int]] = {}
    by_admin: dict[str, list[int]] = {}
    for r in rows:
        amount, sure = _amount(r)
        r["sum"], r["sure"] = amount, sure
        total += amount
        if sure:
            exact_sum += amount
        else:
            approx_sum += amount
        plan = r.get("plan") or "pro"
        by_plan.setdefault(plan, [0, 0])
        by_plan[plan][0] += 1
        by_plan[plan][1] += amount
        who = person(r.get("admin_name"), r.get("admin_username"), r.get("granted_by"))
        by_admin.setdefault(who, [0, 0])
        by_admin[who][0] += 1
        by_admin[who][1] += amount
    return {"rows": rows, "total": total, "exact": approx_sum == 0, "exact_sum": exact_sum,
            "approx_sum": approx_sum, "by_plan": by_plan, "by_admin": by_admin}


async def boss_view():
    """👑 Boss paneli - hamma narsa bir joyda: bugungi holat, pul, jamoa, va barcha bo'limlar tugmalari."""
    m, admins, st, pending = await asyncio.gather(_money(30), db.list_admins(), db.get_stats(),
                                                  db.count_pending_payments())  # parallel - bitta kutish
    plan_line = f"⭐ {st['standard']} • 💎 {st['pro']} • 👑 {st['vip']}"
    approx = "" if m["exact"] else " <i>(≈ bir qismi)</i>"
    wait = f"⏳ Kutilayotgan cheklar: <b>{pending}</b> — tasdiqlang 👇" if pending else "✅ Kutilayotgan chek yo'q"
    lines = [
        "👑 <b>BOSS PANELI</b>",
        LINE,
        "📅 <b>Bugun</b>",
        f"   👤 Yangi o'quvchilar: <b>+{st['new_today']}</b> • 🎯 Tekshiruvlar: <b>{st['today']}</b>",
        f"   {wait}",
        "",
        "💰 <b>Oxirgi 30 kun</b>",
        f"   Tushum: <b>{plans.money(m['total'])}</b>{approx} • {len(m['rows'])} ta obuna",
        f"   Faol obunachilar: <b>{st['premium']}</b> ({plan_line})",
        "",
        "👥 <b>O'quvchilar</b>",
        f"   Jami: <b>{st['users']}</b> • hafta: +{st['new_week']} • faol (7 kun): {st['active_week']}",
        f"   📚 Mocklar: <b>{st['mocks']}</b> • 🎯 30 kunda tekshiruv: {st['month']}",
        "",
        f"👮 <b>Jamoa:</b> {len(ADMIN_IDS)} boss • {len(admins)} admin",
        LINE,
        "<i>Hamma bo'lim shu yerda. Tez ochish: /boss</i>",
    ]
    B = _b
    rows = []
    if WEBAPP_URL.startswith("https://"):
        rows.append([InlineKeyboardButton(text="🖥 Boss sayti — to'liq ekran",
                                          web_app=WebAppInfo(url=f"{WEBAPP_URL}/boss?t={int(time.time())}"))])
    rows += [
        [B("💰 Tushum hisoboti", "boss_money"), B(f"⏳ Cheklar ({pending})" if pending else "🧾 Cheklar", "boss_chk")],
        [B("👮 Adminlarni boshqarish", "adm_admins")],
        [B("⚙️ Tariflar, narx, karta", "adm_settings"), B("🩺 Tizim holati", "boss_sys")],
        [B("📚 Mocklar", "adm_exams"), B("👥 Foydalanuvchilar", "adm_users:0")],
        [B("💎 Premium", "adm_prem"), B("📊 Natijalar", "results:0")],
        [B("📣 Xabar yuborish", "adm_bc"), B("📈 Statistika", "adm_stats")],
    ]
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def _boss(callback: CallbackQuery) -> bool:
    return is_owner(callback.from_user.id)


@router.callback_query(F.data == "boss")
async def boss_menu(callback: CallbackQuery, state: FSMContext):
    if not _boss(callback):
        return await callback.answer("Faqat Boss uchun.", show_alert=True)
    await state.clear()
    text, kb = await boss_view()
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "boss_chk")
async def boss_checks(callback: CallbackQuery):
    """Ko'rib chiqilmagan to'lov cheklari: bosilsa - chek rasmi tasdiqlash tugmalari bilan qayta yuboriladi."""
    if not _boss(callback):
        return await callback.answer("Faqat Boss uchun.", show_alert=True)
    rows = await db.pending_payments(10)
    lines = ["🧾 <b>Kutilayotgan to'lov cheklari</b>", LINE]
    kb = []
    if not rows:
        lines.append("Hammasi ko'rib chiqilgan ✅")
    for p in rows:
        lines.append(f"#{p['id']} • {person(p.get('full_name'), p.get('username'), p['telegram_id'])} — "
                     f"{plans.name(p.get('plan') or 'standard')} • {_date(p['created_at'])}")
        kb.append([_b(f"🧾 #{p['id']} — {(p.get('full_name') or str(p['telegram_id']))[:24]}", f"pchk:{p['id']}")])
    if rows:
        lines.append("\nChekni bosing — rasmi tasdiqlash tugmalari bilan chiqadi.")
    kb.append([_b("⬅️ Boss paneli", "boss")])
    await callback.message.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await callback.answer()


SHOW_BUYERS = 15


@router.callback_query(F.data == "boss_money")
async def boss_money(callback: CallbackQuery):
    if not _boss(callback):
        return await callback.answer("Faqat Boss uchun.", show_alert=True)
    m = await _money(30)
    lines = ["💰 <b>Tushum hisoboti — oxirgi 30 kun</b>", LINE,
             f"💵 Jami tushum: <b>{plans.money(m['total'])}</b> ({len(m['rows'])} ta obuna)"]
    if m["approx_sum"]:
        lines.append(f"   • aniq to'langan: {plans.money(m['exact_sum'])}")
        lines.append(f"   • taxminiy ≈: {plans.money(m['approx_sum'])} — qo'lda berilgan, summasi yozilmagan obunalar")
    if m["by_plan"]:
        lines.append("\n📦 <b>Tariflar bo'yicha:</b>")
        for p in plans.PAID:
            if p in m["by_plan"]:
                n, total = m["by_plan"][p]
                lines.append(f"   {plans.name(p)} — {n} ta, {plans.money(total)}")
    if m["rows"]:
        lines.append("\n🧾 <b>Kim sotib oldi</b> (eng yangisi birinchi):")
        for i, r in enumerate(m["rows"][:SHOW_BUYERS], 1):
            mark = "" if r["sure"] else " ≈"
            lines.append(
                f"<b>{i}. {person(r.get('buyer_name'), r.get('buyer_username'), r.get('telegram_id'))}</b>\n"
                f"   {plans.name(r.get('plan') or 'pro')} • {r.get('days')} kun • <b>{plans.money(r['sum'])}</b>{mark}\n"
                f"   📅 {_date(r['created_at'])} da berildi • {_date(r.get('until'))} gacha\n"
                f"   ✔️ Tasdiqladi: {person(r.get('admin_name'), r.get('admin_username'), r.get('granted_by'))}")
        if len(m["rows"]) > SHOW_BUYERS:
            lines.append(f"\n<i>… yana {len(m['rows']) - SHOW_BUYERS} ta — to'liq ro'yxat «🖥 Boss sayti»da.</i>")
        lines.append("\n👮 <b>Adminlar bo'yicha</b> (kim nechta obunani tasdiqladi):")
        for who, (n, total) in sorted(m["by_admin"].items(), key=lambda x: -x[1][0]):
            lines.append(f"   • {who} — {n} ta, {plans.money(total)}")
        if m["approx_sum"]:
            lines.append("\n<i>≈ — to'lov summasi yozilmagan (obuna qo'lda berilgan): tarif narxi × muddat bo'yicha "
                         "hisoblandi. Masalan, 365 kunlik Pro ≈ 12 oylik Pro narxi.</i>")
    else:
        lines.append("\nOxirgi 30 kunda obuna sotilmagan.")
    await callback.message.edit_text(_fit(lines), parse_mode="HTML",
                                     reply_markup=InlineKeyboardMarkup(inline_keyboard=[[_b("⬅️ Boss paneli", "boss")]]))
    await callback.answer()


def _fit(lines: list[str], limit: int = 4000) -> str:
    """Telegram chegarasi: sig'maydigan qatorlar tashlanadi (har qatordagi HTML teglar o'zida yopilgan)."""
    out, size = [], 0
    for line in lines:
        if size + len(line) + 1 > limit:
            out.append("<i>… davomi «🖥 Boss sayti»da.</i>")
            break
        out.append(line)
        size += len(line) + 1
    return "\n".join(out)


@router.callback_query(F.data == "boss_sys")
async def boss_system(callback: CallbackQuery):
    if not _boss(callback):
        return await callback.answer("Faqat Boss uchun.", show_alert=True)
    import ops  # ops bot.py ni import qiladi - aylana importdan qochish uchun shu yerda
    text = await ops.human_status(callback.bot)
    await callback.message.edit_text(text[:4000], parse_mode="HTML",
                                     reply_markup=InlineKeyboardMarkup(inline_keyboard=[[_b("⬅️ Boss paneli", "boss")]]))
    await callback.answer()


# ---------- Admin huquqlari ----------

async def perms_view(uid: int):
    user, act, admins = await asyncio.gather(db.get_user(uid), db.admin_activity(30), db.list_admins())
    user = user or {}
    row = next((x for x in admins if x["telegram_id"] == uid), {})
    name = person(user.get("full_name"), user.get("username"), uid)
    perms = roles.perms_of(uid)
    a = act.get(uid) or {}
    lines = [f"🔐 <b>{name}</b> — admin", LINE,
             f"🆔 <code>{uid}</code> • qo'shilgan: {_date(row.get('added_at'))}",
             f"📈 30 kunda: {a.get('grants', 0)} ta obuna berdi, {a.get('checks', 0)} ta chekni ko'rib chiqdi",
             f"🔓 Ochiq bo'limlar: <b>{len(perms)}</b> / {len(roles.SECTIONS)}", "",
             "Bo'limni bosib yoqing (✅) yoki yoping (❌):"]
    rows = [[_b(f"{'✅' if key in perms else '❌'} {label}", f"prm:{uid}:{key}")]
            for key, (label, _) in roles.SECTIONS.items()]
    rows.append([_b("✅ Hammasini ochish", f"prm:{uid}:all"), _b("❌ Hammasini yopish", f"prm:{uid}:none")])
    rows.append([_b("🗑 Adminlikdan olish", f"adr:{uid}")])
    rows.append([_b("⬅️ Adminlar", "adm_admins")])
    lines.append("\n<i>💎 Premium ochiq bo'lsa — to'lov cheklari ham shu adminga keladi. Tariflar, narxlar, "
                 "karta va adminlarni faqat Boss boshqaradi.</i>")
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("prm:"))
async def perms_edit(callback: CallbackQuery):
    if not _boss(callback):
        return await callback.answer("Faqat Boss uchun.", show_alert=True)
    bits = callback.data.split(":")
    uid = int(bits[1]) if len(bits) > 1 and bits[1].lstrip("-").isdigit() else 0
    if not uid or is_owner(uid) or uid not in {a["telegram_id"] for a in await db.list_admins()}:
        return await callback.answer("Admin topilmadi.", show_alert=True)
    if len(bits) == 3:
        current = roles.perms_of(uid)
        key = bits[2]
        if key == "all":
            current = set(roles.SECTIONS)
        elif key == "none":
            current = set()
        elif key in roles.SECTIONS:
            current ^= {key}
        await db.set_admin_perms(uid, None if current == set(roles.SECTIONS) else roles.encode(current))
    text, kb = await perms_view(uid)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer("Saqlandi" if len(bits) == 3 else None)
