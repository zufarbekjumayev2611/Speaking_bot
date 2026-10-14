"""👑 Boss paneli (faqat ADMIN_IDS dagi asosiy adminlar): tushum, adminlar va ularning huquqlari,
tarif/narx sozlamalari, tizim holati. Adminlar ro'yxati va qo'shish/o'chirish - premium.py da."""
import html
import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

import db
import plans
import roles
from config import ADMIN_IDS, is_owner

router = Router()
LINE = "━━━━━━━━━━━━━━━━"


def e(value) -> str:
    return html.escape(str(value or ""), quote=False)


def _b(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _amount(row: dict) -> tuple[int, bool]:
    """(summa, aniqmi): admin izohda summani yozgan bo'lsa - o'sha, aks holda tarif narxi bo'yicha taxmin."""
    digits = re.sub(r"\D", "", row.get("amount") or "")
    if len(digits) >= 4:
        return int(digits), True
    price = plans.price_for(row.get("plan") or "pro")
    return round(price * max(1, row.get("days") or 30) / plans.PERIOD_DAYS), False


async def _money(days: int = 30) -> dict:
    rows = await db.grants_since(days)
    total, exact, by_plan, by_admin = 0, True, {}, {}
    for r in rows:
        amount, sure = _amount(r)
        total += amount
        exact = exact and sure
        by_plan[r.get("plan") or "pro"] = by_plan.get(r.get("plan") or "pro", 0) + 1
        who = r.get("admin_name") or str(r.get("granted_by") or "?")
        by_admin[who] = by_admin.get(who, 0) + 1
    return {"rows": rows, "total": total, "exact": exact, "by_plan": by_plan, "by_admin": by_admin}


async def boss_view():
    m = await _money(30)
    admins = await db.list_admins()
    stats = await db.get_stats()
    plan_line = " • ".join(f"{plans.PLANS[p]['icon']} {m['by_plan'].get(p, 0)}" for p in plans.PAID)
    approx = "" if m["exact"] else " <i>(taxminiy)</i>"
    lines = [
        "👑 <b>Boss paneli</b>",
        LINE,
        f"💰 30 kunlik tushum: <b>{plans.money(m['total'])}</b>{approx}",
        f"🧾 Berilgan obunalar: <b>{len(m['rows'])}</b> ta ({plan_line})",
        f"💎 Faol obunachilar: <b>{stats['premium']}</b> • 👥 O'quvchilar: <b>{stats['users']}</b>",
        f"👮 Adminlar: <b>{len(ADMIN_IDS)}</b> boss + <b>{len(admins)}</b> admin",
        LINE,
        "Adminlarning huquqlarini, tarif va narxlarni shu yerdan boshqarasiz.",
    ]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [_b("👮 Adminlar va huquqlar", "adm_admins")],
        [_b("💰 Tushum hisoboti", "boss_money"), _b("⚙️ Tariflar va narxlar", "adm_settings")],
        [_b("🩺 Tizim holati", "boss_sys")],
        [_b("⬅️ Admin panel", "admin")],
    ])
    return "\n".join(lines), kb


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


@router.callback_query(F.data == "boss_money")
async def boss_money(callback: CallbackQuery):
    if not _boss(callback):
        return await callback.answer("Faqat Boss uchun.", show_alert=True)
    m = await _money(30)
    lines = ["💰 <b>Tushum — oxirgi 30 kun</b>", LINE, f"Jami: <b>{plans.money(m['total'])}</b>"
             + ("" if m["exact"] else "\n<i>Izohida summa yozilmagan obunalar tarif narxi bo'yicha hisoblandi.</i>")]
    if m["by_admin"]:
        lines.append("\n<b>Kim nechta obuna berdi:</b>")
        lines += [f"• {e(name)} — {n} ta" for name, n in sorted(m["by_admin"].items(), key=lambda x: -x[1])]
    if m["rows"]:
        lines.append("\n<b>Oxirgilari:</b>")
        for r in m["rows"][:10]:
            amount, sure = _amount(r)
            lines.append(f"• {r['created_at'][:10]} — {plans.name(r.get('plan') or 'pro')}, {r.get('days')} kun — "
                         f"{plans.money(amount)}{'' if sure else ' ≈'}")
    else:
        lines.append("\nHali obuna berilmagan.")
    await callback.message.edit_text("\n".join(lines)[:4000], parse_mode="HTML",
                                     reply_markup=InlineKeyboardMarkup(inline_keyboard=[[_b("⬅️ Boss paneli", "boss")]]))
    await callback.answer()


@router.callback_query(F.data == "boss_sys")
async def boss_system(callback: CallbackQuery):
    if not _boss(callback):
        return await callback.answer("Faqat Boss uchun.", show_alert=True)
    import ops  # ops bot.py ni import qiladi - aylana importdan qochish uchun shu yerda
    ping = await db.ping()
    text = (f"🩺 <b>Tizim holati</b>\n{LINE}\n{ops.instance_line()}\nBaza javobi: <b>{ping * 1000:.0f} ms</b>\n\n"
            "<i>To'liq ma'lumot: /status</i>")
    await callback.message.edit_text(text[:4000], parse_mode="HTML",
                                     reply_markup=InlineKeyboardMarkup(inline_keyboard=[[_b("⬅️ Boss paneli", "boss")]]))
    await callback.answer()


# ---------- Admin huquqlari ----------

async def perms_view(uid: int):
    user = await db.get_user(uid) or {}
    name = e(user.get("full_name") or uid)
    perms = roles.perms_of(uid)
    lines = [f"🔐 <b>{name}</b> — huquqlar", LINE, "Bo'limni bosib yoqing (✅) yoki yoping (❌):"]
    rows = [[_b(f"{'✅' if key in perms else '❌'} {label}", f"prm:{uid}:{key}")]
            for key, (label, _) in roles.SECTIONS.items()]
    rows.append([_b("✅ Hammasini ochish", f"prm:{uid}:all"), _b("❌ Hammasini yopish", f"prm:{uid}:none")])
    rows.append([_b("🗑 Adminlikdan olish", f"adr:{uid}")])
    rows.append([_b("⬅️ Adminlar", "adm_admins")])
    lines.append("\n<i>Tariflar, narxlar va adminlarni faqat Boss boshqaradi.</i>")
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
