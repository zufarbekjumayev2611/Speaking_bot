"""Speaking va writing'ni Bilimni baholash agentligining RASMIY (yangi format)
baholash mezonlari bo'yicha baholash:
  - har bir qism (yoki xat) rasmiy tavsiflar asosida YAXLIT ball oladi
    (speaking 1.1/1.2/2: 0-5, speaking 3: 0-6; writing 1-xat/2-xat: 0-5, 2-qism: 0-6);
  - RATERS > 1 bo'lsa - bir necha mustaqil "ekspert" ballarining o'rtachasi olinadi;
  - ekspert bahosi rasmiy jadval bo'yicha 75 ballik standart ballga o'tkaziladi.
Baholovchi: Groq (standart) yoki Claude - .env dagi GRADER_PROVIDER bo'yicha."""
import asyncio
import html
import json
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
)
from languages import LANGUAGES
from netclient import get_session
from parts import part_info
from scoring import MAX_SCORE, level_for, standard_score

LANG = LANGUAGES[EXAM_LANGUAGE]

GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
CLAUDE_URL = "https://api.anthropic.com/v1/messages"

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
  "mistakes": [{{"wrong": "...", "correct": "...", "note": "qisqa izoh"}}],
  "tips": ["tavsiya 1", "tavsiya 2"]
}}
band - 0 dan {max_band} gacha BUTUN son, faqat yuqoridagi mezon tavsifiga mos ravishda.
notes - har bir jihat bo'yicha 1 gaplik izoh. mistakes - eng muhim ko'pi bilan 5 ta xato.
tips - ko'pi bilan 3 ta amaliy tavsiya. Barcha izohlar O'ZBEK tilida (lotin yozuvida)."""


def _json_tail(kind: str, max_band: int) -> str:
    notes = ", ".join(f'"{k}": "..."' for k in notes_for(kind))
    return _JSON_TAIL.format(notes=notes, max_band=max_band)


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
Xatolar: faqat gapirganda eshitiladigan ({LANG['mistake_types']}). "wrong" - transkripsiyadan
so'zma-so'z parcha, "correct" - undan HAQIQATAN farq qiladigan to'g'ri shakl; ikkalasi bir xil
bo'lsa yoki ishonching komil bo'lmasa - kiritma. Tavsiyalar og'zaki nutq uchun: tayyor iboralar ({LANG['phrases']}),
o'ylab olish iboralari ({LANG['thinking_phrases']}). Imlo haqida maslahat BERMA.

{_json_tail("speaking", p['max'])}"""


def writing_prompt(component: dict) -> str:
    return f"""Sen {LANG['lang_name']} bo'yicha Bilimni baholash agentligining ko'p darajali
(multilevel) imtihonida YOZMA ISH eksperti vazifasini bajarasan.

MUHIM: bu YOZMA ish - o'quvchi matnni klaviaturada YOZGAN. Talaffuz, ovoz, nutq tezligi
yoki ravonlik haqida HECH NARSA yozma - ular bu yerda baholanmaydi.

BAHOLANADIGAN MATN: {component['name']}. Uslub: {component['style']}. Tavsiya etilgan hajm:
{component['words']}. Ball quyidagi RASMIY mezon bo'yicha, butun matnga yaxlit qo'yiladi:
{component['rubric']}

{LANG.get('writing_style_note', '')}
Senga topshiriq matni, o'quvchi matni va so'zlar soni beriladi. Hajm, uslub (rasmiy/norasmiy),
topshiriqdagi barcha bandlar yoritilgani, grammatika ({LANG['grammar_focus']}), imlo va
punktuatsiya, lug'at va bog'lovchi vositalarni mezon tavsifiga solishtir.

XATOLAR RO'YXATI QOIDALARI (juda muhim):
- "wrong" - o'quvchi matnidan SO'ZMA-SO'Z ko'chirilgan parcha bo'lsin;
- "correct" - undan HAQIQATAN farq qiladigan to'g'ri shakl bo'lsin. To'g'ri yozilgan so'zni
  hech qachon xato deb ko'rsatma; "wrong" va "correct" bir xil bo'lsa - uni kiritma;
- xato ekaniga ishonching komil bo'lmasa - kiritma. Xato topilmasa, ro'yxat bo'sh qolsin;
- so'zni o'qilishi / talaffuzi bo'yicha qayta yozma, faqat imlo qoidasi bo'yicha to'g'rila;
- agar o'quvchi {LANG['lang_adj'].lower()} maxsus harflar o'rniga oddiy lotin harflarini yozgan bo'lsa
  (masalan klaviaturada bo'lmagani uchun), buni har bir so'zda alohida xato qilma - bitta umumiy
  izoh sifatida ayt.
Xato turlari: grammatik, leksik va imlo ({LANG['mistake_types']}).
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
        "max_completion_tokens": 2500,
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
        "max_tokens": 1500,
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
    result["band"] = band
    result["notes"] = {k: str(notes.get(k) or "") for k in note_keys}
    return result


def _norm(text) -> str:
    """Solishtirish uchun: kichik harf, tinish belgilari va bo'shliqlarsiz."""
    return re.sub(r"[\W_]+", "", str(text or "").casefold())


def _soft(text) -> str:
    """Faqat katta-kichik harf, bo'shliq va oxiridagi nuqta/vergul farqini e'tiborsiz qoldiradi."""
    return re.sub(r"\s+", " ", str(text or "").casefold()).strip(" .,!?;:")


def _clean_mistakes(mistakes: list, source: str) -> list:
    """AI ba'zan to'g'ri so'zni "xato" deb ko'rsatadi (wrong == correct) yoki matnda yo'q
    narsani yozadi. Bunday yozuvlar o'quvchini chalg'itmasligi uchun olib tashlanadi."""
    src = _norm(source)
    clean = []
    for m in mistakes or []:
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
        clean.append(m)
    return clean[:5]


async def _grade_component(kind: str, system: str, user: str, max_band: int, raters: int | None = None) -> dict:
    """`raters` (standart RATERS) ta mustaqil baholash; ekspertlar ballarining o'rtachasi (0.5 aniqlikda)."""
    note_keys = list(notes_for(kind))
    outcomes = await asyncio.gather(
        *[_grade_once(system, user, max_band, note_keys) for _ in range(raters or RATERS)], return_exceptions=True
    )
    ok = [o for o in outcomes if isinstance(o, dict)]
    if not ok:
        raise outcomes[0]
    avg = round(sum(o["band"] for o in ok) / len(ok) * 2) / 2
    first = ok[0]
    return {
        "band": avg,
        "raters": len(ok),
        "reason": str(first.get("reason") or ""),
        "notes": first["notes"],
        "mistakes": _clean_mistakes(first.get("mistakes"), user),
        "tips": [str(t) for t in (first.get("tips") or [])][:3],
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
        mistakes += c.pop("mistakes", [])
        tips += c.pop("tips", [])
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
        "mistakes": mistakes[:5],
        "tips": tips[:3],
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
    blocks = []
    for a in answers:
        photo = " (savolda rasm bor edi)" if a.get("has_photo") else ""
        transcript = (a.get("transcript") or "").strip()
        blocks.append(
            f"{a['position']}-savol{photo}: {a['question']}\n"
            f"{_metrics(transcript, a.get('duration_sec'), a.get('answer_sec'))}\n"
            f"O'quvchi nutqi (transkripsiya): {transcript or '(hech narsa eshitilmadi)'}"
        )
    comp = await _grade_component("speaking", speaking_prompt(part), "\n\n".join(blocks), p["max"], raters)
    comp.update(name=p["name"], max=p["max"], label=_label(p["labels"], comp["band"]))
    return _combine("speaking", part, p["name"], [comp])


# ---------------------------------------------------------------- writing

async def grade_writing(task_text: str, texts: dict, part: str | None, raters: int | None = None) -> dict:
    """texts: {"email1": "...", "email2": "..."} (1-qism) yoki {"essay": "..."} (2-qism)."""
    p = part_info("writing", part)
    comps = p["components"]

    async def one(c):
        answer = (texts.get(c["key"]) or "").strip()
        words = len(answer.split())
        user = f"TOPSHIRIQ:\n{task_text}\n\nO'QUVCHI MATNI - {c['name']} ({words} so'z):\n{answer or '(javob yozilmagan)'}"
        res = await _grade_component("writing", writing_prompt(c), user, c["max"], raters)
        res.update(name=c["name"], max=c["max"], words=words, label=_label(c["labels"], res["band"]))
        return res

    results = await asyncio.gather(*[one(c) for c in comps])
    return _combine("writing", part, p["name"], list(results))


# ---------------------------------------------------------------- natija xabari

def result_message(title: str, part_name: str, r: dict) -> str:
    e = lambda v: html.escape(str(v or ""), quote=False)  # AI matnidagi < > belgilar HTML'ni buzmasin
    icon = "✍️" if r.get("kind") == "writing" else "🎙"
    note_labels = notes_for(r.get("kind", "speaking"))
    lines = [
        f"{icon} <b>{e(title)}</b>",
        f"<i>{e(part_name)}</i>\n",
    ]
    for c in r["components"]:
        words = f", {c['words']} so'z" if c.get("words") is not None else ""
        lines.append(f"📌 <b>{e(c['name'])}: {float(c['band']):g}/{c['max']}</b> ({e(c['label'])}{words})")
        if c.get("reason"):
            lines.append(f"<i>{e(c['reason'])}</i>")
        for key, label in note_labels.items():
            if c.get("notes", {}).get(key):
                lines.append(f"• {label}: {e(c['notes'][key])}")
        lines.append("")
    if r.get("total") is not None:
        lines.append(
            f"🎯 Ekspert bahosi: <b>{float(r['raw']):g}/{r['max_raw']}</b> → standart ball "
            f"<b>{r['total']}/{r.get('max_total', MAX_SCORE)}</b> ({e(r['level'])})"
        )
    elif len(r["components"]) > 1:
        lines.append(f"🎯 Qism bo'yicha jami: <b>{float(r['raw']):g}/{r['max_raw']}</b>")
    if r.get("mistakes"):
        lines.append("\n<b>Asosiy xatolar:</b>")
        for m in r["mistakes"]:
            lines.append(f"❌ {e(m.get('wrong'))} → ✅ {e(m.get('correct'))}\n    <i>{e(m.get('note'))}</i>")
    if r.get("tips"):
        lines.append("\n<b>Tavsiyalar:</b>")
        lines += [f"• {e(t)}" for t in r["tips"]]
    lines.append(
        "\n<i>Baho Bilimni baholash agentligining rasmiy mezonlari asosida sun'iy intellekt "
        "tomonidan qo'yildi." + (" Talaffuz baholanmaydi." if r.get("kind") != "writing" else "") + "</i>"
    )
    return "\n".join(lines)