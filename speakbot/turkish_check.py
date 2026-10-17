"""Turkcha unli uyg'unligi istisnolari - AI'ga ishonmasdan, dastur o'zi tekshiradi.

Arabcha / fransuzcha o'zlashgan ba'zi so'zlar oxirgi bo'g'ini qalin bo'lsa ham qo'shimchani INGICHKA unli bilan
oladi: saat -> saati, saate, saatine (saatı, saata, saatına - XATO); rol -> rolü (rolu - XATO). AI bu
istisnolarni ko'pincha bilmaydi: o'quvchining xatosini o'tkazib yuboradi yoki o'zi noto'g'ri "to'g'ri"
variantni taklif qiladi. Bu modul:
  1) AI taklif qilgan "to'g'ri" variantdagi shunday xatoni tuzatadi;
  2) o'quvchi matnidagi shunday xatoni AI ko'rmagan bo'lsa ham ro'yxatga qo'shadi."""
import re

# (unli bilan boshlanadigan qo'shimcha oldidagi o'zak, undosh bilan boshlanadigan qo'shimcha oldidagi o'zak, lug'at shakli)
STEMS = [
    ("saat", "saat", "saat"), ("dikkat", "dikkat", "dikkat"), ("hakikat", "hakikat", "hakikat"),
    ("harf", "harf", "harf"), ("hayal", "hayal", "hayal"), ("ihtimal", "ihtimal", "ihtimal"),
    ("istikbal", "istikbal", "istikbal"), ("kabul", "kabul", "kabul"), ("kalb", "kalp", "kalp"),
    ("rol", "rol", "rol"), ("gol", "gol", "gol"), ("alkol", "alkol", "alkol"), ("kontrol", "kontrol", "kontrol"),
    ("petrol", "petrol", "petrol"), ("sembol", "sembol", "sembol"), ("protokol", "protokol", "protokol"),
]

# Qo'shimchalar "qalin" (xato) shaklda; I - ı yoki u, A - a, D - d/t (o'zakka qarab)
SUFFIXES = [
    "I", "A", "In", "IA", "InA", "InDA", "InDAn", "InI", "IylA", "Im", "ImI", "ImA", "ImDA", "ImDAn", "ImIz",
    "ImIzA", "ImIzDA", "ImIzI", "InIz", "InIzA", "InIzI", "lAr", "lArA", "lArDA", "lArDAn", "lArI", "lArInA",
    "lArInDA", "lArInI", "lArImIz", "lArI", "DA", "DAn", "DAki", "lA", "ylA", "sIz", "lI",
]

_FRONT = {"a": "e", "ı": "i", "o": "ö", "u": "ü"}
_VOWELS = "aeıioöuü"


def _expand(stem_v: str, stem_c: str, suffix: str) -> list[tuple[str, str]]:
    """(xato shakl, to'g'ri shakl) juftliklari bitta qo'shimcha uchun."""
    stem = stem_v if suffix[0] in "IAy" else stem_c
    rounded = [ch for ch in stem if ch in _VOWELS][-1] in "ou"
    out = []
    for i_wrong in (("ı", "u") if "I" in suffix else ("ı",)):
        wrong, right = stem, stem
        for ch in suffix:
            if ch == "I":
                wrong += i_wrong
                right += "ü" if rounded else "i"
            elif ch == "A":
                wrong += "a"
                right += "e"
            elif ch == "D":  # undosh moslashuvi: jarangsiz undoshdan keyin -t (saatte), aks holda -d (saatlerde)
                d = "t" if wrong[-1] in "pçtkfhsş" else "d"
                wrong += d
                right += d
            else:
                wrong += ch
                right += ch
        out.append((wrong, right))
    return out


WRONG: dict[str, str] = {}
for _v, _c, _base in STEMS:
    for _s in SUFFIXES:
        for _w, _r in _expand(_v, _c, _s):
            WRONG.setdefault(_w, _r)
_BASE = {w: next(base for v, c, base in STEMS if w.startswith(v) or w.startswith(c)) for w in WRONG}

_WORD = re.compile(r"[A-Za-zÇĞİIÖŞÜçğıiöşüâîû]+")


def _lower(word: str) -> str:
    return word.replace("I", "ı").replace("İ", "i").lower()


def _match_case(src: str, fixed: str) -> str:
    return fixed[:1].upper() + fixed[1:] if src[:1].isupper() else fixed


def fix_text(text: str) -> str:
    """Matndagi istisno xatolarni tuzatadi (AI'ning "to'g'ri" varianti uchun)."""
    def repl(m):
        w = m.group(0)
        fixed = WRONG.get(_lower(w))
        return _match_case(w, fixed) if fixed else w
    return _WORD.sub(repl, text or "")


def find_errors(text: str) -> list[dict]:
    """O'quvchi matnidagi istisno xatolar: [{"wrong", "correct", "note"}] (har so'z bir marta)."""
    out, seen = [], set()
    for m in _WORD.finditer(text or ""):
        w = m.group(0)
        low = _lower(w)
        if low in WRONG and low not in seen:
            seen.add(low)
            base = _BASE[low]
            out.append({
                "wrong": w, "correct": _match_case(w, WRONG[low]),
                "note": f"«{base}» o'zlashgan so'z (istisno): qo'shimchasi ingichka unli (e, i, ü) bilan qo'shiladi — "
                        f"{WRONG[low]}.",
            })
    return out


def check_mistakes(mistakes: list[dict], student_text: str) -> list[dict]:
    """AI ro'yxatini tuzatadi: "to'g'ri" variantdagi istisno xatolar tuzatiladi; tuzatilgach o'quvchi matni
    bilan bir xil bo'lib qolgan (aslida xato bo'lmagan) yozuvlar olib tashlanadi; o'quvchi matnidagi
    istisno xatolar AI ko'rmagan bo'lsa - qo'shiladi."""
    out = []
    for m in mistakes:
        correct = fix_text(m.get("correct", ""))
        if _lower(correct).strip() == _lower(m.get("wrong", "")).strip():
            continue
        out.append({**m, "correct": correct})
    covered = " ".join(_lower(m.get("wrong", "")) for m in out)
    for err in find_errors(student_text):
        if _lower(err["wrong"]) not in covered:
            out.insert(0, err)
    return out
