"""Rollar: 👑 Boss (ADMIN_IDS dagi asosiy adminlar) va adminlar (botda qo'shilganlar).

Boss hamma narsani qila oladi va adminlarning huquqlarini bo'limma-bo'lim belgilaydi. Adminga yopiq
bo'limning tugmalari unga ko'rinmaydi; eski xabardagi tugmani bossa ham - markaziy filtr (middleware)
to'xtatadi. Faqat Boss: adminlar, tarif/narx sozlamalari, Boss paneli."""
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery

import config
from config import is_admin, is_owner

# bo'lim -> (nomi, shu bo'limga tegishli tugma prefikslari)
SECTIONS: dict[str, tuple[str, tuple[str, ...]]] = {
    "tests": ("📚 Mocklar va testlar", (
        "adm_exams", "mka:", "mkap:", "mkat:", "mksw", "mksh", "mka_new", "mka_find", "mkad:", "mkady:", "mkar:", "exs:", "ex_find", "exam:", "exam_new", "nk:", "np:", "exam_del", "exam_ren:", "exam_toggle:",
        "q_new:", "q_done:", "q_edit:", "q_delask:", "q_del:", "qe_", "qs_", "qall:", "qap:", "qaa:",
        "wtask:", "wtf:", "wph:", "wphx:", "wprev:",
    )),
    "users": ("👥 Foydalanuvchilar", ("adm_users:", "usr:", "usr_bl:", "usr_search")),
    "premium": ("💎 Premium", ("adm_prem", "pg_start", "pgp:", "pgd:", "pgu:", "pr_start", "prv:", "usr_rv:", "pay_ok:", "pay_no:", "pay_nr:", "pay_nb:", "pchk:")),
    "results": ("📊 Natijalar", ("results:", "rpdf:")),
    "broadcast": ("📣 Xabar yuborish", ("adm_bc", "bc_")),
    "stats": ("📈 Statistika", ("adm_stats",)),
}
BOSS_ONLY = ("boss", "adm_admins", "ad_add", "adr:", "adrd:", "adm_settings", "lim:", "pi_edit", "prm:", "card_edit", "ac_edit")


def perms_of(user_id: int) -> set[str]:
    """Adminning ochiq bo'limlari (Boss - hammasi)."""
    if is_owner(user_id):
        return set(SECTIONS)
    if not is_admin(user_id):
        return set()
    custom = config.ADMIN_PERMS.get(user_id)
    return set(SECTIONS) if custom is None else set(custom) & set(SECTIONS)


def can(user_id: int, section: str) -> bool:
    return section in perms_of(user_id)


def section_of(data: str) -> str | None:
    """Tugma qaysi bo'limga tegishli ("boss" - faqat Boss uchun)."""
    if any(data == p or data.startswith(p) for p in BOSS_ONLY):
        return "boss"
    best, length = None, 0
    for key, (_, prefixes) in SECTIONS.items():
        for p in prefixes:
            if (data == p or data.startswith(p)) and len(p) > length:
                best, length = key, len(p)
    return best


def encode(perms: set[str]) -> str:
    return ",".join(k for k in SECTIONS if k in perms)


def decode(raw: str | None) -> set[str] | None:
    """None - cheklanmagan (hamma bo'lim)."""
    if raw is None:
        return None
    return {p for p in raw.split(",") if p in SECTIONS}


class PermissionMiddleware(BaseMiddleware):
    """Adminga yopiq bo'lim tugmasini bosganda - ogohlantirish va to'xtatish (o'quvchilarga ta'sir qilmaydi)."""

    async def __call__(self, handler, event: CallbackQuery, data):
        uid = event.from_user.id if event.from_user else 0
        if is_admin(uid) and not is_owner(uid) and event.data:
            section = section_of(event.data)
            if section == "boss" or (section and not can(uid, section)):
                await event.answer("🔒 Bu bo'lim sizga yopiq. Kerak bo'lsa, Boss'dan ruxsat so'rang.", show_alert=True)
                return None
        return await handler(event, data)
