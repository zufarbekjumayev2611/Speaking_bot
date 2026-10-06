"""Speaking va writing'ni Bilimni baholash agentligining RASMIY (yangi format)
baholash mezonlari bo'yicha baholash:
  - har bir qism (yoki xat) rasmiy tavsiflar asosida YAXLIT ball oladi
    (speaking 1.1/1.2/2: 0-5, speaking 3: 0-6; writing 1-xat/2-xat: 0-5, 2-qism: 0-6);
  - RATERS > 1 bo'lsa - bir necha mustaqil "ekspert" ballarining o'rtachasi olinadi
    (topilgan xatolar esa birlashtiriladi);
  - ekspert bahosi rasmiy jadval bo'yicha 75 ballik standart ballga o'tkaziladi.
Baholovchi: Groq (standart) yoki Claude - .env dagi GRADER_PROVIDER bo'yicha."""
import asyncio
import html
import json
import logging
import re

import aiohttp

from config import (
    ANTHROPIC_API_KEY,
    CLAUDE_MODEL,
    EXAM_LANGUAGE,
    GRADER_MODEL,
    GRADER_PROVIDER,
    GROQ_API_KEY,
    RATERS,
    all_admin_ids,
)
from languages import LANGUAGES
from netclient import get_session
from parts import LANGUAGE_FUNCTIONS, part_info
from scoring import MAX_SCORE, level_for, standard_score

LANG = LANGUAGES[EXAM_LANGUAGE]
log = logging.getLogger("grader")

GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
CLAUDE_URL = "https://api.anthropic.com/v1/messages"

MAX_MISTAKES = 10        # har bir qism / matn uchun ko'rsatiladigan xatolar soni
MAX_TIPS = 3             # har bir qism / matn uchun tavsiyalar
MAX_TIPS_TOTAL = 5       # natijadagi jami tavsiyalar
TG_LIMIT = 4000          # Telegram xabari 4096 belgidan oshmasin

SPEAKING_NOTES = {
    "grammar": "Grammatika",
    "vocabulary": "So'z boyligi",
    "fluency": "Ravonlik",
    "coherence": "Izchillik",
}
WRITING_NOTES = {
    "task": "Topshiriq va uslub",
    "grammar": "Grammatika va imlo",
    "vocabulary": "So'z boyligi",
    "coherence": "Izchillik va bog'lovchilar",
}


def notes_for(kind: str) -> dict:
    return WRITING_NOTES if kind == "writing" else SPEAKING_NOTES


class GraderError(RuntimeError):
    def __init__(self, status: int, detail: str):
        super().__init__(f"{GRADER_PROVIDER} {status}: {detail}")
        self.status = status


# ---------------------------------------------------------------- promptlar

_JSON_TAIL = """FAQAT quyidagi JSON'ni qaytar, boshqa hech narsa yozma:
{{
  "band": 0,
  "reason": "nega aynan shu ball: mezon tavsifiga tayangan holda 1-2 gap",
  "notes": {{{notes}}},
  "mistakes": [{{"wrong": "...", "correct": "...", "note": "qisqa izoh: qanday xato va qoidasi"}}],
  "tips": ["tavsiya 1", "tavsiya 2"]
}}
band - 0 dan {max_band} gacha BUTUN son, faqat yuqoridagi mezon tavsifiga mos ravishda.
notes - har bir jihat bo'yicha 1-2 gaplik aniq izoh (nima yaxshi, nima yetishmaydi).
mistakes - {source} boshidan oxirigacha tekshirib topilgan HAQIQIY xatolar, eng muhimidan boshlab,
ko'pi bilan {max_mistakes} ta. Xato ko'p bo'lsa - {max_mistakes} tagacha yoz; kam bo'lsa - faqat borini yoz,
to'qib chiqarma; xato bo'lmasa - bo'sh ro'yxat. Bir xil xato takrorlansa - bir marta yoz.
tips - ko'pi bilan {max_tips} ta amaliy tavsiya. Barcha izohlar O'ZBEK tilida (lotin yozuvida)."""


def _json_tail(kind: str, max_band: int) -> str:
    notes = ", ".join(f'"{k}": "..."' for k in notes_for(kind))
    source = "matnni" if kind == "writing" else "nutqni"
    return _JSON_TAIL.format(notes=notes, max_band=max_band, max_mistakes=MAX_MISTAKES, max_tips=MAX_TIPS, source=source)


def speaking_prompt(part: str | None) -> str:
    p = part_info("speaking", part)
    return f"""Sen {LANG['lang_name']} bo'yicha Bilimni baholash agentligining ko'p darajali
(multilevel) imtihonida GAPIRISH bo'limi eksperti vazifasini bajarasan.

TOPSHIRIQ: {p['name']}. Ball quyidagi RASMIY mezon bo'yicha, butun javobga yaxlit qo'yiladi:
{p['rubric']}

MUHIM: o'quvchi mikrofonga GAPIRGAN - senga uning nutqining avtomatik transkripsiyasi beriladi.
1. Tinish belgilari, katta-kichik harf va imloni baholama - ularni dastur qo'ygan.
2. Kontekstga to'g'ri kelmaydigan, lekin tovushi yaqin so'z - ehtimol dastur xatosi.
3. Talaffuzni eshitmaysan: mezondagi talaffuz bandini NEYTRAL deb hisobla va ballni
   boshqa belgilar (mavzuga moslik, grammatika, lug'at, ravonlik, bog'lanish) bo'yicha qo'y.
4. {LANG['fillers']}, takror va tuzatishlar - to'xtalishlar belgisi (ravonlikka ta'sir qiladi).
5. Har bir javob uchun o'lchovlar beriladi: yozuv davomiyligi, so'zlar soni, nutq tezligi,
   vaqtdan foydalanish. {LANG['wpm_note']} Juda qisqa javob - mavzu yoritilmagan.
6. "Savolga javob mavzu bo'yicha" - javob shu savolga mos va mazmunli bo'lsa. Nechta
   savolga mavzu bo'yicha javob berilganini SANAB, ballni shunga qarab aniqla.

Grammatikada ayniqsa: {LANG['grammar_focus']}.
Xatolar: faqat gapirganda eshitiladigan ({LANG['mistake_types']}). Barcha javoblarni boshidan
oxirigacha ko'rib chiq. "wrong" - transkripsiyadan so'zma-so'z parcha, "correct" - undan HAQIQATAN
farq qiladigan to'g'ri shakl; ikkalasi bir xil bo'lsa yoki ishonching komil bo'lmasa - kiritma.
Tavsiyalar og'zaki nutq uchun: tayyor iboralar ({LANG['phrases']}),
o'ylab olish iboralari ({LANG['thinking_phrases']}). Imlo haqida maslahat BERMA.

{_json_tail("speaking", p['max'])}"""


def writing_prompt(component: dict) -> str:
    return f"""Sen {LANG['lang_name']} bo'yicha Bilimni baholash agentligining ko'p darajali
(multilevel) imtihonida YOZMA ISH eksperti vazifasini bajarasan.

MUHIM: bu YOZMA ish - o'quvchi matnni klaviaturada YOZGAN. Talaffuz, ovoz, nutq tezligi
yoki ravonlik haqida HECH NARSA yozma - ular bu yerda baholanmaydi.

BAHOLANADIGAN MATN: {component['name']}. Uslub: {component['style']}. Tavsiya etilgan hajm:
{component['words']}.
Topshiriq xususiyatlari (rasmiy spetsifikatsiya): {component.get('spec', '')}
Ko'zda tutilgan til funksiyalari: {LANGUAGE_FUNCTIONS}.

Ball quyidagi RASMIY mezon bo'yicha, butun matnga yaxlit qo'yiladi:
{component['rubric']}

{LANG.get('writing_style_note', '')}
Senga topshiriq, o'quvchi matni va so'zlar soni beriladi. Mezonga solishtir:
1. Topshiriq bajarilishi: ko'rsatmadagi adresat (kimga) va maqsad (nima uchun) hamda kelgan xatdagi
   BARCHA bandlarga javob berilganmi; yoritilmagan bandlarni "task" izohida aniq ayt.
2. Uslub: {component['style']} (murojaat, yakunlash iboralari, rasmiy / norasmiy til).
3. Hajm: so'zlar soni mezondagi hajm chegaralariga mosmi.
4. Grammatika ({LANG['grammar_focus']}), imlo va punktuatsiya.
5. So'z boyligi, bog'lovchi vositalar, paragraf va matn tuzilishi.

XATOLAR RO'YXATI QOIDALARI (juda muhim):
- "wrong" - o'quvchi matnidan SO'ZMA-SO'Z ko'chirilgan parcha bo'lsin (topshiriq matnidan emas);
- "correct" - undan HAQIQATAN farq qiladigan to'g'ri shakl bo'lsin. To'g'ri yozilgan so'zni
  hech qachon xato deb ko'rsatma; "wrong" va "correct" bir xil bo'lsa - uni kiritma;
- xato ekaniga ishonching komil bo'lmasa - kiritma. Xato topilmasa, ro'yxat bo'sh qolsin;
- so'zni o'qilishi / talaffuzi bo'yicha qayta yozma, faqat imlo qoidasi bo'yicha to'g'rila;
- agar o'quvchi {LANG['lang_adj'].lower()} maxsus harflar o'rniga oddiy lotin harflarini yozgan bo'lsa
  (masalan klaviaturada bo'lmagani uchun), buni har bir so'zda alohida xato qilma - bitta umumiy
  izoh sifatida ayt.
Xato turlari: grammatik, leksik, imlo va punktuatsiya, uslub ({LANG['mistake_types']}).
Tavsiyalar yozma ish uchun: tuzilma, uslub, bog'lovchi iboralar ({LANG['writing_phrases']}).

{_json_tail("writing", component['max'])}"""


# ---------------------------------------------------------------- AI so'rovlari

def _extract_json(text: str) -> dict:
    text = re.sub(r"```(?:json)?", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start : end + 1])


async def _ask_groq(system: str, user: str, json_mode: bool) -> str:
    payload = {
        "model": GRADER_MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0.2,
        "max_completion_tokens": 4096,  # 10 tagacha xato + izohlar (+ modelning fikrlash tokenlari)
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    if "gpt-oss" in GRADER_MODEL:
        payload["reasoning_effort"] = "low"
    async with get_session().post(
        GROQ_CHAT_URL,
        json=payload,
        headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
        timeout=aiohttp.ClientTimeout(total=90),
    ) as resp:
        body = await resp.json(content_type=None)
        if resp.status != 200:
            raise GraderError(resp.status, str(body)[:500])
        return body["choices"][0]["message"]["content"] or ""


async def _ask_claude(system: str, user: str) -> str:
    payload = {
        "model": CLAUDE_MODEL,
        "max_tokens": 3000,
        "temperature": 0.2,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    async with get_session().post(
        CLAUDE_URL,
        json=payload,
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        timeout=aiohttp.ClientTimeout(total=90),
    ) as resp:
        body = await resp.json(content_type=None)
        if resp.status != 200:
            raise GraderError(resp.status, str(body)[:500])
        return "".join(b.get("text", "") for b in body.get("content", []) if b.get("type") == "text")


async def _ask(system: str, user: str) -> str:
    if GRADER_PROVIDER == "claude":
        return await _ask_claude(system, user)
    try:
        return await _ask_groq(system, user, json_mode=True)
    except GraderError as e:
        if e.status != 400:  # ba'zi modellar JSON rejimini qo'llamaydi
            raise
        return await _ask_groq(system, user, json_mode=False)


async def _grade_once(system: str, user: str, max_band: int, note_keys: list[str]) -> dict:
    result = _extract_json(await _ask(system, user))
    try:
        band = max(0, min(max_band, round(float(result.get("band", 0)))))
    except (TypeError, ValueError):
        band = 0
    notes = result.get("notes") or {}
    if not isinstance(notes, dict):
        notes = {}
    result["band"] = band
    result["notes"] = {k: str(notes.get(k) or "") for k in note_keys}
    return result


def _norm(text) -> str:
    """Solishtirish uchun: kichik harf, tinish belgilari va bo'shliqlarsiz."""
    return re.sub(r"[\W_]+", "", str(text or "").casefold())


def _soft(text) -> str:
    """Faqat katta-kichik harf, bo'shliq va oxiridagi nuqta/vergul farqini e'tiborsiz qoldiradi."""
    return re.sub(r"\s+", " ", str(text or "").casefold()).strip(" .,!?;:")


def _clean_mistakes(mistakes, source: str) -> list:
    """AI ba'zan to'g'ri so'zni "xato" deb ko'rsatadi (wrong == correct) yoki o'quvchi matnida yo'q
    narsani yozadi. Bunday yozuvlar o'quvchini chalg'itmasligi uchun olib tashlanadi."""
    src = _norm(source)
    clean = []
    for m in mistakes if isinstance(mistakes, list) else []:
        if not isinstance(m, dict):
            continue
        wrong, correct = _norm(m.get("wrong")), _norm(m.get("correct"))
        if not wrong or not correct:
            continue
        # tenglikni apostrof va harflarni saqlagan holda tekshiramiz ("Taşkentte" ≠ "Taşkent'te")
        if _soft(m.get("wrong")) == _soft(m.get("correct")):
            continue
        if src and wrong not in src:
            continue
        clean.append({"wrong": str(m.get("wrong")).strip(), "correct": str(m.get("correct")).strip(),
                      "note": str(m.get("note") or "").strip()})
    return clean


def _merge_mistakes(lists: list, source: str) -> list:
    """Bir necha ekspert topgan xatolarni birlashtiradi (takrorlarsiz), ko'pi bilan MAX_MISTAKES ta."""
    seen, merged = set(), []
    for mistakes in lists:
        for m in _clean_mistakes(mistakes, source):
            key = _norm(m["wrong"])
            if key in seen:
                continue
            seen.add(key)
            merged.append(m)
    return merged[:MAX_MISTAKES]


async def _grade_component(kind: str, system: str, user: str, max_band: int, raters: int | None = None,
                           source: str = "") -> dict:
    """`raters` (standart RATERS) ta mustaqil baholash; ekspertlar ballarining o'rtachasi (0.5 aniqlikda).
    `source` - o'quvchining o'z matni / nutqi: xatolar faqat shu yerda bo'lsa qabul qilinadi."""
    note_keys = list(notes_for(kind))
    outcomes = await asyncio.gather(
        *[_grade_once(system, user, max_band, note_keys) for _ in range(raters or RATERS)], return_exceptions=True
    )
    ok = [o for o in outcomes if isinstance(o, dict)]
    if not ok:
        raise outcomes[0]
    avg = round(sum(o["band"] for o in ok) / len(ok) * 2) / 2
    first = ok[0]
    tips = first.get("tips") if isinstance(first.get("tips"), list) else []
    return {
        "band": avg,
        "raters": len(ok),
        "reason": str(first.get("reason") or ""),
        "notes": first["notes"],
        "mistakes": _merge_mistakes([o.get("mistakes") for o in ok], source or user),
        "tips": [str(t) for t in tips if str(t).strip()][:MAX_TIPS],
    }


def _label(labels: dict, band: float) -> str:
    """Butun ball - bitta daraja; o'rtacha x.5 bo'lsa - ikki qo'shni daraja oralig'i."""
    low, high = int(band), int(band + 0.5)
    if low == high:
        return labels.get(low, "")
    return f"{labels.get(low, '')} – {labels.get(high, '')}"


def _combine(kind: str, part: str | None, part_name: str, components: list[dict]) -> dict:
    raw = sum(c["band"] for c in components)
    max_raw = sum(c["max"] for c in components)
    # 75 ballik standart ball faqat bo'lim TO'LIQ bajarilganda ma'noga ega (writing: 16 ball).
    # Alohida qism uchun rasmiy natija - shu qismning ekspert bahosi va uning darajasi.
    full = kind == "writing" and max_raw == 16
    total = standard_score(raw, max_raw) if full else None
    mistakes, tips = [], []
    for c in components:
        mistakes += c.get("mistakes", [])
    # tavsiyalar navbat bilan: har bir matndan bittadan, takrorlarsiz
    for i in range(MAX_TIPS):
        for c in components:
            t = (c.get("tips") or [])[i:i + 1]
            if t and t[0] not in tips:
                tips.append(t[0])
    return {
        "kind": kind,
        "part": part,
        "part_name": part_name,
        "components": components,
        "raw": raw,
        "max_raw": max_raw,
        "total": total,
        "max_total": MAX_SCORE,
        "level": level_for(total) if full else " / ".join(c["label"] for c in components),
        "mistakes": mistakes,
        "tips": tips[:MAX_TIPS_TOTAL],
    }


# ---------------------------------------------------------------- speaking

def _metrics(transcript: str, duration, allotted) -> str:
    words = len(transcript.split())
    duration = float(duration or 0)
    info = [f"so'zlar soni: {words}"]
    if duration >= 3:
        info.append(f"yozuv davomiyligi: {duration:.0f} s")
        info.append(f"nutq tezligi: ~{words / (duration / 60):.0f} so'z/daqiqa")
    if allotted:
        info.append(f"ajratilgan vaqt: {allotted} s")
        if duration >= 3:
            info.append(f"vaqtdan foydalanish: {min(100, duration / allotted * 100):.0f}%")
    return "O'lchovlar: " + ", ".join(info)


async def grade_speaking(answers: list[dict], part: str | None, raters: int | None = None) -> dict:
    """answers: [{"position", "question", "transcript", "duration_sec", "answer_sec", "has_photo"}]"""
    p = part_info("speaking", part)
    # Part 1.2: rasm(lar) 4-6-savollar davomida ko'rinib turadi
    shared_photo = p.get("carry_photo") and any(a.get("has_photo") for a in answers)
    blocks = []
    for a in answers:
        photo = " (savolda rasm bor edi)" if a.get("has_photo") or shared_photo else ""
        transcript = (a.get("transcript") or "").strip()
        blocks.append(
            f"{a['position']}-savol{photo}: {a['question']}\n"
            f"{_metrics(transcript, a.get('duration_sec'), a.get('answer_sec'))}\n"
            f"O'quvchi nutqi (transkripsiya): {transcript or '(hech narsa eshitilmadi)'}"
        )
    source = "\n".join((a.get("transcript") or "") for a in answers)
    comp = await _grade_component("speaking", speaking_prompt(part), "\n\n".join(blocks), p["max"], raters, source)
    comp.update(name=p["name"], max=p["max"], label=_label(p["labels"], comp["band"]))
    return _combine("speaking", part, p["name"], [comp])


# ---------------------------------------------------------------- writing

async def grade_writing(tasks, texts: dict, part: str | None, raters: int | None = None) -> dict:
    """tasks: {"email1": "...", ...} - har bir matn uchun topshiriq (yoki bitta umumiy matn - str).
    texts: {"email1": "...", "email2": "..."} (1-qism), {"essay": "..."} (2-qism) yoki uchalasi (to'liq)."""
    p = part_info("writing", part)
    comps = p["components"]

    async def one(c):
        answer = (texts.get(c["key"]) or "").strip()
        words = sum(1 for w in answer.split() if re.search(r"\w", w))
        if not answer:  # yozilmagan matn - mezon bo'yicha 0 ball, AI'ga so'rov yuborilmaydi
            res = {"band": 0, "raters": 0, "reason": "Javob yozilmagan.", "notes": {}, "mistakes": [], "tips": []}
        else:
            task = tasks.get(c["key"], "") if isinstance(tasks, dict) else str(tasks or "")
            user = (
                f"TOPSHIRIQ:\n{task}\n\n"
                f"O'QUVCHI MATNI - {c['name']} ({words} so'z; talab: {c['words']}):\n{answer}"
            )
            res = await _grade_component("writing", writing_prompt(c), user, c["max"], raters, answer)
        res.update(name=c["name"], max=c["max"], words=words, label=_label(c["labels"], res["band"]))
        return res

    results = await asyncio.gather(*[one(c) for c in comps])
    return _combine("writing", part, p["name"], list(results))


# ---------------------------------------------------------------- natija xabari

def _e(value) -> str:
    """AI matnidagi < > belgilar HTML'ni buzmasin; qator ko'chishlari bitta qatorga (xabar bo'linganda teg buzilmasin)."""
    return html.escape(re.sub(r"\s*\n\s*", " ", str(value or "")).strip(), quote=False)


def _mistake_lines(mistakes: list) -> list[str]:
    return [f"❌ {_e(m.get('wrong'))} → ✅ {_e(m.get('correct'))}" + (f"\n    <i>{_e(m.get('note'))}</i>" if m.get("note") else "")
            for m in mistakes]


def result_message(title: str, part_name: str, r: dict) -> str:
    icon = "✍️" if r.get("kind") == "writing" else "🎙"
    note_labels = notes_for(r.get("kind", "speaking"))
    many = len(r["components"]) > 1
    lines = [
        f"{icon} <b>{_e(title)}</b>",
        f"<i>{_e(part_name)}</i>\n",
    ]
    for c in r["components"]:
        words = f", {c['words']} so'z" if c.get("words") is not None else ""
        lines.append(f"📌 <b>{_e(c['name'])}: {float(c['band']):g}/{c['max']}</b> ({_e(c['label'])}{words})")
        if c.get("reason"):
            lines.append(f"<i>{_e(c['reason'])}</i>")
        for key, label in note_labels.items():
            if c.get("notes", {}).get(key):
                lines.append(f"• {label}: {_e(c['notes'][key])}")
        if many and c.get("mistakes"):
            lines.append(f"<b>Xatolar ({len(c['mistakes'])}):</b>")
            lines += _mistake_lines(c["mistakes"])
        lines.append("")
    if r.get("total") is not None:
        lines.append(
            f"🎯 Ekspert bahosi: <b>{float(r['raw']):g}/{r['max_raw']}</b> → standart ball "
            f"<b>{r['total']}/{r.get('max_total', MAX_SCORE)}</b> ({_e(r['level'])})"
        )
    elif many:
        lines.append(f"🎯 Qism bo'yicha jami: <b>{float(r['raw']):g}/{r['max_raw']}</b>")
    if r.get("elapsed_sec"):
        minutes = max(1, round(r["elapsed_sec"] / 60))
        limit = f" (tavsiya: {r['time_min']} daqiqa)" if r.get("time_min") else ""
        lines.append(f"⏱ Sarflangan vaqt: {minutes} daqiqa{limit}")
    if not many and r.get("mistakes"):
        lines.append(f"\n<b>Asosiy xatolar ({len(r['mistakes'])}):</b>")
        lines += _mistake_lines(r["mistakes"])
    if r.get("tips"):
        lines.append("\n<b>Tavsiyalar:</b>")
        lines += [f"• {_e(t)}" for t in r["tips"]]
    lines.append(
        "\n<i>Baho Bilimni baholash agentligining rasmiy mezonlari asosida sun'iy intellekt "
        "tomonidan qo'yildi." + (" Talaffuz baholanmaydi." if r.get("kind") != "writing" else "") + "</i>"
    )
    return "\n".join(lines)


def split_message(text: str, limit: int = TG_LIMIT) -> list[str]:
    """Uzun HTML xabarni qatorlar chegarasida bo'laklarga ajratadi (har bir qatordagi teglar o'zida yopiladi)."""
    parts, current = [], ""
    for line in text.split("\n"):
        while len(line) > limit:  # juda uzun bitta qator (amalda bo'lmaydi) - teglarsiz bo'lib yuboriladi
            if current:
                parts.append(current)
                current = ""
            parts.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            parts.append(current)
            current = line
        else:
            current = candidate
    if current.strip():
        parts.append(current)
    return [p for p in parts if p.strip()]


async def send_long(bot, chat_id: int, text: str):
    for chunk in split_message(text):
        await bot.send_message(chat_id, chunk, parse_mode="HTML")


async def notify_admins(bot, text: str):
    for admin_id in all_admin_ids():
        try:
            await bot.send_message(admin_id, text, parse_mode="HTML")
        except Exception:
            pass


async def report_key_problem(bot, err: Exception):
    """Baholovchi kaliti ishlamasa (401/403) - adminlarga xabar beradi."""
    if getattr(err, "status", None) in (401, 403):
        await notify_admins(bot, f"⚠️ Baholovchi ({GRADER_PROVIDER}) kaliti ishlamayapti - imtihonlar baholanmayapti.")
