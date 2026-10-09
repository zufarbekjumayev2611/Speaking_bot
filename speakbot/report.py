"""Natijalar PDF hisoboti (admin uchun).

Tuzilishi - o'quvchilar ko'p bo'lsa ham tez topiladi:
1) umumiy sonlar;
2) «O'quvchilar» jadvali - alifbo tartibida, har bir o'quvchi BITTA qatorda (urinishlar soni, oxirgi va
   eng yaxshi natija, oxirgi faollik);
3) «Batafsil» - har bir o'quvchi alohida sarlavha ostida, uning barcha natijalari sana bo'yicha.
Shrift - DejaVu Sans (o'zbek va turk harflari: ş ğ ı ç ö ü o‘ g‘ to'g'ri chiqadi)."""
import io
import json
import os
from datetime import datetime, timedelta, timezone

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

from parts import part_info

FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
TZ = timezone(timedelta(hours=5))  # Toshkent vaqti
KIND = {"speaking": "Konuşma", "writing": "Yazma"}

ACCENT = colors.HexColor("#1F4E79")
LIGHT = colors.HexColor("#EEF3F8")
GRID = colors.HexColor("#C9D3DD")
MUTED = colors.HexColor("#5B6770")

_fonts_ready = False


def _fonts():
    global _fonts_ready
    if not _fonts_ready:
        pdfmetrics.registerFont(TTFont("DejaVu", os.path.join(FONT_DIR, "DejaVuSans.ttf")))
        pdfmetrics.registerFont(TTFont("DejaVu-Bold", os.path.join(FONT_DIR, "DejaVuSans-Bold.ttf")))
        pdfmetrics.registerFontFamily("DejaVu", normal="DejaVu", bold="DejaVu-Bold")
        _fonts_ready = True


def _style(name, size=9, bold=False, color=colors.black, leading=None, **kw):
    return ParagraphStyle(name, fontName="DejaVu-Bold" if bold else "DejaVu", fontSize=size,
                          leading=leading or size * 1.3, textColor=color, alignment=TA_LEFT, **kw)


def _esc(text) -> str:
    return (str(text if text is not None else "")).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _local(stamp: str | None) -> datetime | None:
    try:
        return datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).astimezone(TZ)
    except (TypeError, ValueError):
        return None


def _date(stamp: str | None) -> str:
    dt = _local(stamp)
    return dt.strftime("%d.%m.%Y %H:%M") if dt else "—"


local_date = _date  # bot.py dan ham ishlatiladi


def score(r: dict) -> tuple[str, float]:
    """(ko'rinishi, foizi 0..1) - eng yaxshi natijani tanlash uchun foiz ishlatiladi."""
    try:
        res = json.loads(r.get("result_json") or "{}")
        raw, max_raw = float(res["raw"]), float(res["max_raw"])
        if res.get("total") is not None:
            return f"{raw:g}/{max_raw:g} → {res['total']}/75", float(res["total"]) / 75
        return f"{raw:g}/{max_raw:g}", raw / max_raw if max_raw else 0.0
    except (ValueError, KeyError, TypeError):
        s = r.get("score") or 0
        return f"{s}/75", s / 75


def _part(r: dict) -> str:
    name = part_info(r["kind"], r.get("part")).get("name") or r.get("part") or ""
    return name.split(" — ")[0]


_SORT = str.maketrans("şçöüğıİŞÇÖÜĞʻʼ‘’'", "scougiISCOUG     ")


def _student_key(r: dict):
    """Alifbo tartibi: Ş/Ç/Ö/Ü/Ğ/I harflari S/C/O/U/G/I qatorida (oxirida emas)."""
    name = (r.get("full_name") or "").strip().translate(_SORT).replace(" ", "").casefold()
    return (name or "~", r["telegram_id"])


def _summary(rows: list[dict]) -> list[dict]:
    people: dict[int, dict] = {}
    for r in rows:  # rows - eng yangisi oldin
        p = people.setdefault(r["telegram_id"], {"row": r, "speaking": [], "writing": []})
        p[r["kind"]].append(r)
    return sorted(people.values(), key=lambda p: _student_key(p["row"]))


def _cell(items: list[dict], small) -> Paragraph:
    if not items:
        return Paragraph("—", small)
    last, _ = score(items[0])
    best_row = max(items, key=lambda x: score(x)[1])
    best, _ = score(best_row)
    text = f"<b>{len(items)}</b> ta • oxirgi: {_esc(last)}"
    if best_row is not items[0]:
        text += f"<br/>eng yaxshi: <b>{_esc(best)}</b>"
    return Paragraph(text, small)


def _table(data, widths, header_rows=1, zebra=True):
    t = Table(data, colWidths=widths, repeatRows=header_rows)
    style = [
        ("BACKGROUND", (0, 0), (-1, header_rows - 1), ACCENT),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.4, GRID),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]
    if zebra:
        style += [("BACKGROUND", (0, i), (-1, i), LIGHT) for i in range(header_rows + 1, len(data), 2)]
    t.setStyle(TableStyle(style))
    return t


def build_pdf(rows: list[dict], period: str) -> bytes:
    _fonts()
    title_s = _style("t", 16, True, ACCENT, spaceAfter=2)
    sub_s = _style("s", 9, color=MUTED)
    h_s = _style("h", 12, True, ACCENT, spaceBefore=8, spaceAfter=4)
    person_s = _style("p", 10, True, colors.black, spaceBefore=6, spaceAfter=2)
    cell = _style("c", 8.5)
    small = _style("sm", 8)
    head = _style("hd", 8.5, True, colors.white)
    num = _style("n", 8.5, color=MUTED)

    buf = io.BytesIO()
    page = landscape(A4)
    now = datetime.now(TZ).strftime("%d.%m.%Y %H:%M")

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("DejaVu", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(12 * mm, 7 * mm, f"Natijalar hisoboti • {period} • {now}")
        canvas.drawRightString(page[0] - 12 * mm, 7 * mm, f"{doc.page}-sahifa")
        canvas.restoreState()

    doc = SimpleDocTemplate(buf, pagesize=page, leftMargin=12 * mm, rightMargin=12 * mm, topMargin=12 * mm,
                            bottomMargin=13 * mm, title="Natijalar hisoboti", author="Speaking bot")
    width = page[0] - 24 * mm
    people = _summary(rows)
    n_sp = sum(r["kind"] == "speaking" for r in rows)
    n_wr = len(rows) - n_sp

    story = [
        Paragraph("Natijalar hisoboti", title_s),
        Paragraph(f"Davr: <b>{_esc(period)}</b> • tayyorlangan: {now} (Toshkent vaqti)", sub_s),
        Spacer(1, 4 * mm),
    ]
    stats = [[Paragraph(f"<b>{v}</b><br/>{k}", _style("st", 10, color=ACCENT, leading=14)) for k, v in (
        ("o'quvchi", len(people)), ("jami natija", len(rows)), (f"{KIND['speaking']}", n_sp), (f"{KIND['writing']}", n_wr))]]
    st = Table(stats, colWidths=[width / 4] * 4)
    st.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), LIGHT), ("BOX", (0, 0), (-1, -1), 0.4, GRID),
                            ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.white), ("TOPPADDING", (0, 0), (-1, -1), 6),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 6), ("LEFTPADDING", (0, 0), (-1, -1), 8)]))
    story.append(st)

    if not rows:
        story += [Spacer(1, 8 * mm), Paragraph("Bu davrda tugallangan natija yo'q.", cell)]
        doc.build(story, onFirstPage=footer, onLaterPages=footer)
        return buf.getvalue()

    # 1) o'quvchilar - bitta qatorda
    story.append(Paragraph("1. O'quvchilar (alifbo tartibida)", h_s))
    data = [[Paragraph(x, head) for x in ("№", "O'quvchi", "Telegram", KIND["speaking"], KIND["writing"], "Oxirgi faollik")]]
    for i, p in enumerate(people, 1):
        r = p["row"]
        tg = (f"@{_esc(r['username'])}<br/>" if r.get("username") else "") + f"ID {r['telegram_id']}"
        data.append([Paragraph(str(i), num), Paragraph(f"<b>{_esc(r.get('full_name') or 'Nomaʼlum')}</b>", cell),
                     Paragraph(tg, small), _cell(p["speaking"], small), _cell(p["writing"], small),
                     Paragraph(_date(r["created_at"]), small)])
    story.append(_table(data, [10 * mm, 62 * mm, 42 * mm, 58 * mm, 58 * mm, width - 230 * mm]))

    # 2) batafsil - har bir o'quvchi alohida
    story += [PageBreak(), Paragraph("2. Batafsil natijalar (har bir o'quvchi alohida)", h_s)]
    for i, p in enumerate(people, 1):
        r = p["row"]
        name = _esc(r.get("full_name") or "Nomaʼlum")
        user = f" • @{_esc(r['username'])}" if r.get("username") else ""
        items = sorted(p["speaking"] + p["writing"], key=lambda x: x["created_at"] or "", reverse=True)
        data = [[Paragraph(x, head) for x in ("Sana", "Bo'lim", "Qism", "Test / mavzu", "Ball", "Daraja")]]
        for it in items:
            sc, _ = score(it)
            data.append([Paragraph(_date(it["created_at"]), small), Paragraph(KIND.get(it["kind"], ""), small),
                         Paragraph(_esc(_part(it)), small), Paragraph(_esc(it.get("title") or "o'chirilgan test"), small),
                         Paragraph(f"<b>{_esc(sc)}</b>", small), Paragraph(_esc(it.get("level") or ""), small)])
        block = [Paragraph(f"{i}. {name}{user} • ID {r['telegram_id']} — {len(items)} ta natija", person_s),
                 _table(data, [32 * mm, 22 * mm, 28 * mm, 74 * mm, 38 * mm, width - 194 * mm])]
        story.append(KeepTogether(block) if len(items) <= 12 else block[0])
        if len(items) > 12:
            story.append(block[1])
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
