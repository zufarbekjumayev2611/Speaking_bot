"""Ekspert bahosini 75 ballik standart ballga o'tkazish.

Manba: Bilimni baholash agentligi, "Chet tilidan yozma ish topshiriqlarining xususiyatlari"
(yangi format) - "Expert bahosi -> Standard ball" jadvali (0-16, 0.5 qadam bilan).
Writing to'liq bajarilganda (1-xat 5 + 2-xat 5 + 2-qism 6 = 16) bu jadval aynan rasmiy.

Speaking uchun rasmiy o'tkazish jadvali qo'limizda yo'q, shuning uchun speaking va
alohida qismlar uchun ball 16 ballik shkalaga mutanosib o'tkazilib, shu jadval orqali
TAXMINIY standart ball chiqariladi.
"""

WRITING_TABLE = {
    16.0: 75, 15.5: 72, 15.0: 69, 14.5: 67, 14.0: 65, 13.5: 64, 13.0: 63, 12.5: 62,
    12.0: 61, 11.5: 59, 11.0: 57, 10.5: 55, 10.0: 53, 9.5: 51, 9.0: 50, 8.5: 48,
    8.0: 47, 7.5: 45, 7.0: 43, 6.5: 41, 6.0: 40, 5.5: 38, 5.0: 37, 4.5: 35,
    4.0: 33, 3.5: 31, 3.0: 28, 2.5: 25, 2.0: 21, 1.5: 17, 1.0: 14, 0.5: 10, 0.0: 0,
}
TABLE_MAX = 16
MAX_SCORE = 75


def standard_score(raw: float, max_raw: float) -> int:
    """Ekspert bahosi (0..max_raw) -> standart ball (0-75).
    max_raw == 16 bo'lsa - rasmiy jadval aynan qo'llanadi."""
    if max_raw <= 0:
        return 0
    x = max(0.0, min(float(raw), max_raw)) / max_raw * TABLE_MAX
    x = round(x * 2) / 2  # eng yaqin 0.5 ga
    return WRITING_TABLE[x]


def level_for(score: int) -> str:
    """Standart ball bo'yicha daraja (75 ballik shkala)."""
    if score >= 65:
        return "C1"
    if score >= 51:
        return "B2"
    if score >= 38:
        return "B1"
    return "B1 dan quyi"