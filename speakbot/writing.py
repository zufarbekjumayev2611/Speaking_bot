"""✍️ Yazma (writing) bo'limi: topshiriq tuzilishi, so'zlar soni va tekshirish natijasini saqlash.

Manba - Bilimni baholash agentligi PDF'lari ("Chet tilidan yozma ish 1/2-bo'lim topshiriqlarining
xususiyatlari" va baholash mezonlari):
  1-qism: tashkilotdan kelgan 50-80 so'zli elektron xat (javob berilishi kerak bo'lgan 3 ta band) va
          har bir xat uchun 1-2 gapli ko'rsatma: 1-xat do'stga norasmiy (~50 so'z),
          2-xat rahbariyatga rasmiy (120-150 so'z). Tavsiya: ~25 daqiqa.
  2-qism: 30-50 so'zli ko'rsatma (janr + muhokama qilinadigan fikr yoki savol); blog posti, forum posti
          yoki jurnal maqolasi, 180-200 so'z. Tavsiya: ~35 daqiqa.
  Yozma bo'lim jami 60 daqiqa; ekspert bahosi (0-16) rasmiy jadval bo'yicha 75 ballik standart ballga
  o'tkaziladi.

Topshiriq bazada ikki ko'rinishda saqlanadi: questions.text - yig'ma matn (eski kod va baholovchi uchun),
questions.meta - alohida maydonlar (JSON), admin har birini alohida tahrirlay olishi uchun.
"""
import html
import json
import logging
import re

import db
import grader
import plans
from access import acquire_check, get_plan, release_check
from parts import part_info

log = logging.getLogger("writing")

# Admin to'ldiradigan maydonlar. "words" - tavsiya etilgan so'zlar oralig'i (tashqarisida ogohlantiriladi),
# "example" - languages.py dagi namuna kaliti.
FIELDS = {
    "letter": {
        "title": "Vaziyat va kelgan xat (ixtiyoriy)",
        "short": "Kelgan xat",
        "optional": True,  # ba'zi materiallarda har bir xatning vaziyati o'z ko'rsatmasida bo'ladi
        "limit": 1500,
        "words": (45, 110),
        "words_text": "vaziyat + xat ≈ 60–90 so'z, xatning o'zi 50–80 so'z",
        "hint": (
            "O'quvchi javob yozadigan elektron xat (tashkilot, klub, kompaniya, maktab va h.k. yuborgan).\n"
            "• boshida 1 gap — vaziyat: o'quvchi kim va qanday xat oldi;\n"
            "• xat matni <b>50–80 so'z</b>, rasmiy va shaxsiy bo'lmagan uslubda;\n"
            "• xatda o'quvchi javob berishi kerak bo'lgan <b>3 ta band</b> bo'lsin "
            "(muammo, o'zgarish, savol yoki taklif)."
        ),
        "example": "wr_example_letter",
    },
    "inst1": {
        "title": "1-xat ko'rsatmasi (norasmiy)",
        "short": "1-xat ko'rsatmasi",
        "limit": 1200,
        "words": (6, 45),
        "words_text": "1–2 gap",
        "hint": (
            "1–2 gap: xat <b>kimga</b> (do'st, tanish) va <b>nima maqsadda</b> yoziladi "
            "(his-tuyg'ular, rejalar, maslahat...). O'quvchi taxminan <b>50 so'z</b> yozadi."
        ),
        "example": "wr_example_inst1",
    },
    "inst2": {
        "title": "2-xat ko'rsatmasi (rasmiy)",
        "short": "2-xat ko'rsatmasi",
        "limit": 1200,
        "words": (6, 45),
        "words_text": "1–2 gap",
        "hint": (
            "1–2 gap: xat <b>kimga</b> (rahbariyat, menejer, mijozlar xizmati...) va <b>nima maqsadda</b> "
            "(shikoyat, taklif, muqobil variant so'rash...). O'quvchi <b>120–150 so'z</b> yozadi."
        ),
        "example": "wr_example_inst2",
    },
    "essay": {
        "title": "2-qism topshirig'i (blog / maqola)",
        "short": "2-qism topshirig'i",
        "limit": 1000,
        "words": (20, 70),
        "words_text": "30–50 so'z",
        "hint": (
            "Ko'rsatma <b>30–50 so'z</b>:\n"
            "• janr — blog posti, forum posti yoki jurnal maqolasi;\n"
            "• muhokama qilinadigan <b>fikr yoki savol</b>;\n"
            "• hajm talabi: <b>180–200 so'z</b>."
        ),
        "example": "wr_example_essay",
    },
    "text": {  # eski ("Boshqa") mavzular - bitta erkin matn
        "title": "Topshiriq matni",
        "short": "Topshiriq",
        "limit": 2500,
        "words": None,
        "words_text": "",
        "hint": "Topshiriq matnini bitta xabar qilib yuboring.",
        "example": None,
    },
}
PART_FIELDS = {"1": ["letter", "inst1", "inst2"], "2": ["essay"], "full": ["letter", "inst1", "inst2", "essay"]}
# O'quvchi yozadigan har bir matn uchun: (kontekst maydoni, ko'rsatma maydoni)
COMPONENT_FIELDS = {"email1": ("letter", "inst1"), "email2": ("letter", "inst2"), "essay": (None, "essay")}
# Yig'ma matndagi belgilar (eski bazalardagi 1-qism topshiriqlari ham shu ko'rinishda saqlangan)
MARKERS = {"inst1": "✉️ 1-xat ko'rsatmasi:", "inst2": "✉️ 2-xat ko'rsatmasi:", "essay": "📝 2-qism topshirig'i:"}
MAX_TASK_CHARS = 3500
MAX_ANSWER_CHARS = 6000

STATUS_ICON = {"ok": "✅", "short": "⚠️", "long": "⚠️", "critical": "❗", "empty": "❗"}


def e(value) -> str:
    return html.escape(str(value or ""), quote=False)


def fields_for(part: str | None) -> list[str]:
    return PART_FIELDS.get(part or "", ["text"])


def count_words(text: str) -> int:
    """So'zlar soni: harf yoki raqami bor bo'laklar (alohida turgan "-" va "—" sanalmaydi)."""
    return sum(1 for w in (text or "").split() if re.search(r"\w", w))


# ---------------------------------------------------------------- topshiriq: yig'ish / ajratish

def compose(part: str | None, values: dict) -> str:
    """Maydonlardan yig'ma matn: kelgan xat, so'ng belgilangan ko'rsatmalar."""
    chunks = []
    for f in fields_for(part):
        v = (values.get(f) or "").strip()
        if not v:
            continue
        marker = None if (part == "2" and f == "essay") else MARKERS.get(f)
        chunks.append(f"{marker} {v}" if marker else v)
    return "\n\n".join(chunks)[:MAX_TASK_CHARS]


def parse_legacy(part: str | None, text: str) -> dict:
    """Maydonlari saqlanmagan (eski) topshiriqni belgilar bo'yicha bo'laklarga ajratadi."""
    text = (text or "").strip()
    fields = fields_for(part)
    if fields == ["text"]:
        return {"text": text}
    if fields == ["essay"]:
        return {"essay": text}
    found = sorted((text.find(MARKERS[f]), f) for f in fields if f in MARKERS and MARKERS[f] in text)
    if not found:
        return {"letter": text}
    values = {"letter": text[: found[0][0]].strip()}
    for i, (pos, f) in enumerate(found):
        end = found[i + 1][0] if i + 1 < len(found) else len(text)
        values[f] = text[pos + len(MARKERS[f]) : end].strip()
    return values


def task_values(part: str | None, row: dict | None) -> dict:
    """Topshiriq maydonlari {maydon: matn} (bo'sh maydonlar qaytarilmaydi)."""
    if not row:
        return {}
    meta = row.get("meta")
    if meta:
        try:
            data = json.loads(meta)
        except ValueError:
            data = None
        if isinstance(data, dict):
            return {f: str(data[f]).strip() for f in fields_for(part) if str(data.get(f) or "").strip()}
    return {k: v for k, v in parse_legacy(part, row.get("text")).items() if v}


def missing_fields(part: str | None, values: dict, required_only: bool = True) -> list[str]:
    """Kiritilmagan maydonlar; required_only=True - ixtiyoriylari (kelgan xat) hisobga olinmaydi."""
    return [f for f in fields_for(part) if not (values.get(f) or "").strip()
            and not (required_only and FIELDS[f].get("optional"))]


# ---------------------------------------------------------------- butun topshiriqni bitta xabarda kiritish

_N = r"(?:soru|görev|gorev|xat|topshiriq|task|e-?posta|mektup|savol|madde)"
_BULK = {  # qator boshidagi belgilar: «1-Soru», «1. Görev», «1.1», «Task 1.2», «2-qism» ...
    "inst1": [rf"1\s*[-–.)]?\s*{_N}", rf"{_N}\s*[-–]?\s*1\b(?![.,]\d)", r"1\.1\b", r"✉️\s*1-xat"],
    "inst2": [rf"2\s*[-–.)]?\s*{_N}", rf"{_N}\s*[-–]?\s*2\b(?![.,]\d)", r"1\.2\b", r"✉️\s*2-xat"],
    "essay": [rf"3\s*[-–.)]?\s*{_N}", rf"{_N}\s*[-–]?\s*3\b", r"2\s*[-–.)]?\s*(?:qism|bölüm|kısım|part)\b",
              r"(?:part|bölüm|qism)\s*[-–]?\s*2\b(?![.,]\d)", r"📝\s*2-qism"],
}


def split_bulk(part: str | None, text: str) -> dict:
    """Admin butun topshiriqni bitta xabarda yuborsa - «1-Soru / 2-Soru / 3-Soru (yoki 2-qism)» kabi
    belgilar bo'yicha maydonlarga ajratadi. Kamida ikkita belgi topilmasa - {} (oddiy bitta maydon)."""
    fields = fields_for(part)
    wanted = [f for f in ("inst1", "inst2", "essay") if f in fields]
    if len(wanted) < 2:
        return {}
    hits = []
    for f in wanted:
        pattern = re.compile(r"^[ \t*•#>]*(?:" + "|".join(_BULK[f]) + r")", re.IGNORECASE | re.MULTILINE)
        m = pattern.search(text)
        if m:
            hits.append((m.start(), m.end(), f))
    hits.sort()
    if len(hits) < 2 or [h[2] for h in hits] != [f for f in wanted if f in {h[2] for h in hits}]:
        return {}  # tartibi noto'g'ri yoki bitta belgi - ishonchsiz, bo'lmaymiz
    values = {}
    head = text[: hits[0][0]].strip()
    if head and "letter" in fields:
        values["letter"] = head
    for i, (start, end, f) in enumerate(hits):
        stop = hits[i + 1][0] if i + 1 < len(hits) else len(text)
        body = text[end:stop].strip().lstrip(":.-–) ").strip()
        if body:
            values[f] = body
    return values


def component_task(part: str | None, values: dict, comp_key: str) -> tuple[str, str]:
    """(kontekst, ko'rsatma) - o'quvchi shu matnni yozayotganda ko'radigan topshiriq qismi."""
    if fields_for(part) == ["text"]:
        return values.get("text", ""), ""
    ctx_field, inst_field = COMPONENT_FIELDS.get(comp_key, (None, None))
    return (values.get(ctx_field, "") if ctx_field else ""), (values.get(inst_field, "") if inst_field else "")


def grading_task(part: str | None, values: dict, comp_key: str) -> str:
    """Baholovchiga beriladigan topshiriq: kelgan xat + aynan shu xat uchun ko'rsatma."""
    ctx, inst = component_task(part, values, comp_key)
    if ctx and inst:
        return f"Kelgan xat (vaziyat):\n{ctx}\n\nShu matn uchun ko'rsatma: {inst}"
    return ctx or inst


# ---------------------------------------------------------------- so'zlar soni

def length_status(comp: dict, n: int) -> str:
    lo, hi = comp.get("target") or (0, 10**9)
    if n == 0:
        return "empty"
    if n <= comp.get("critical", 0):
        return "critical"
    if n < lo:
        return "short"
    if n > hi * 1.3:
        return "long"
    return "ok"


def words_note(comp: dict, n: int) -> str:
    """O'quvchi yuborgan matn hajmi haqida qisqa izoh."""
    status = length_status(comp, n)
    note = f"📝 {n} so'z"
    if status in ("critical", "empty"):
        note += f" ❗ talab: {comp['words']} — juda qisqa, rasmiy mezonda bunday hajmga past ball qo'yiladi"
    elif status == "short":
        note += f" ⚠️ talab: {comp['words']} — matn qisqa, bu bahoga ta'sir qilishi mumkin"
    elif status == "long":
        note += f" ⚠️ talab: {comp['words']} — tavsiya etilganidan ancha uzun"
    return note


def field_note(field: str, text: str) -> tuple[bool, str]:
    """Admin kiritgan maydonni PDF talablariga solishtirish: (mosmi, izoh)."""
    spec = FIELDS[field]
    n = count_words(text)
    if not spec.get("words"):
        return True, f"📏 {n} so'z"
    lo, hi = spec["words"]
    ok = lo <= n <= hi
    extra = ""
    if field in ("inst1", "inst2") and len(re.findall(r"[.!?]+(?:\s|$)", text.strip())) > 3:
        ok, extra = False, " — 1–2 gap bo'lsin"
    return ok, f"📏 {n} so'z {'✅' if ok else '⚠️'} (tavsiya: {spec['words_text']}){extra}"


# ---------------------------------------------------------------- o'quvchi ko'radigan topshiriq

def component_title(comp: dict) -> str:
    kind = comp["style"].split(" - ")[0]
    return f"{comp['name'].split(' (')[0]} — {kind}, {comp['words']}" if comp["key"] != "essay" else f"{comp['name']} — {comp['words']}"


def task_message(title: str, part: str | None, values: dict) -> str:
    """O'quvchiga yuboriladigan topshiriq (HTML)."""
    info = part_info("writing", part)
    comps = {c["key"]: c for c in info["components"]}
    fields = fields_for(part)
    lines = [f"✍️ <b>{e(title)}</b>", f"<i>{e(info['name'])}</i>", ""]
    if fields == ["text"]:
        lines.append(e(values.get("text")))
    else:
        if values.get("letter"):
            lines += [e(values["letter"]), ""]
        for field, key, icon in (("inst1", "email1", "✉️"), ("inst2", "email2", "✉️")):
            if field in fields:
                lines.append(f"{icon} <b>{e(component_title(comps[key]))}</b>\n{e(values.get(field))}\n")
        if "essay" in fields:
            head = "📝 <b>2-qism: " if part == "full" else "📝 <b>"
            lines.append(f"{head}{e(component_title(comps['essay']))}</b>\n{e(values.get('essay'))}\n")
    if info.get("time"):
        lines.append(f"⏱ Tavsiya etilgan vaqt: <b>{info['time']} daqiqa</b>")
    return "\n".join(lines).strip()


def component_payload(part: str | None, values: dict) -> list[dict]:
    """Mini app uchun: har bir yoziladigan matn - nomi, hajm talabi va unga tegishli topshiriq."""
    out = []
    for c in part_info("writing", part)["components"]:
        ctx, inst = component_task(part, values, c["key"])
        lo, hi = c.get("target") or (0, 0)
        out.append({
            "key": c["key"],
            "name": c["name"],
            "words": c["words"],
            "min": lo,
            "max": hi,
            "wmin": c.get("wmin", 0),
            "critical": c.get("critical", 0),
            "context": ctx,
            "instruction": inst,
        })
    return out


# ---------------------------------------------------------------- tekshirish va saqlash

class WritingDenied(Exception):
    """O'quvchiga ko'rsatiladigan sabab bilan rad etish (limit tugagan, matn yozilmagan...)."""


class WritingBusy(WritingDenied):
    """Aynan shu ish hozir tekshirilmoqda (ikkinchi so'rov)."""


_inflight: set[tuple[int, int]] = set()  # (foydalanuvchi, mavzu) - bitta ish ikki marta baholanmasin


def joined_text(part: str | None, texts: dict) -> str:
    comps = part_info("writing", part)["components"]
    return "\n\n".join(f"[{c['name']}]\n{texts.get(c['key'], '')}" for c in comps)


async def grade_and_store(bot, user_id: int, user_name: str, exam: dict, texts: dict,
                          elapsed_sec: int | None = None) -> tuple[dict, bool]:
    """Ishni tekshiradi, saqlaydi, natijani o'quvchiga va adminlarga yuboradi.
    (natija, yangimi) qaytaradi: aynan shu matn yaqinda tekshirilgan bo'lsa - eski natija (False)
    qaytadi, qayta baholanmaydi va limit sarflanmaydi. Rad etilsa - WritingDenied."""
    part = exam.get("part")
    info = part_info("writing", part)
    comps = info["components"]
    texts = {c["key"]: (texts.get(c["key"]) or "").strip()[:MAX_ANSWER_CHARS] for c in comps}
    if not any(texts.values()):
        raise WritingDenied("Hech qaysi matn yozilmagan.")

    joined = joined_text(part, texts)
    key = (user_id, exam["id"])
    if key in _inflight:
        raise WritingBusy("Ishingiz hozir tekshirilmoqda — natijani bir oz kuting.")
    _inflight.add(key)  # tekshiruv va belgilash orasida await yo'q - parallel so'rov WritingBusy oladi
    reserved = False
    try:
        prev = await db.find_recent_writing(user_id, exam["id"], joined)
        if prev and prev.get("result_json"):
            return json.loads(prev["result_json"]), False
        allowed, reason = await acquire_check(user_id)
        if not allowed:
            raise WritingDenied(reason)
        reserved = True
        row = await db.get_writing_task(exam["id"])
        values = task_values(part, row)
        tasks = {c["key"]: grading_task(part, values, c["key"]) or (row or {}).get("text", "") for c in comps}
        words = sum(count_words(t) for t in texts.values())
        submission_id = await db.create_writing_submission(user_id, exam["id"], joined, words)
        try:
            result = await grader.grade_writing(tasks, texts, part, plans.raters_for(await get_plan(user_id)))
        except Exception as err:
            log.exception("Writing baholash xatosi (submission %s)", submission_id)
            await grader.report_key_problem(bot, err)
            raise
        if elapsed_sec:
            result["elapsed_sec"] = int(elapsed_sec)
        if info.get("time"):
            result["time_min"] = info["time"]
        await db.finish_writing_submission(submission_id, result["total"], result["level"], result["raw"], result)
    finally:
        _inflight.discard(key)
        if reserved:
            release_check(user_id)

    try:
        await grader.send_long(bot, user_id, grader.result_message(exam["title"], info["name"], result))
    except Exception:
        log.exception("Writing natijasini o'quvchiga yuborib bo'lmadi")
    await grader.notify_admins(
        bot,
        f"📥 ✍️ {e(user_name)} (<code>{user_id}</code>) — {e(exam['title'])}: "
        f"<b>{float(result['raw']):g}/{result['max_raw']}</b> ({e(result['level'])})",
    )
    return result, True
