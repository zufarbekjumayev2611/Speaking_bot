"""Ko'p darajali (multilevel) imtihonning speaking va writing qismlari hamda RASMIY
baholash mezonlari (Bilimni baholash agentligi, yangi format).

Speaking:  1.1 (1-3-savollar) 0-5 | 1.2 (4-6-savollar) 0-5 | 2 (7-savol) 0-5 | 3 (8-savol) 0-6
Writing:   1-qism: 1-xat (norasmiy, ~50 so'z) 0-5 + 2-xat (rasmiy, 120-150 so'z) 0-5
           2-qism: blog/forum/maqola (180-200 so'z) 0-6
Har bir ball - butun javob bo'yicha yaxlit (holistik) baho."""

# ============================================================== SPEAKING

_SP_11 = """5 - A2 darajadan yuqori nutq.
4 (yuqori A2) - BARCHA UCHTA savolga javob mavzu bo'yicha; ba'zi oddiy grammatik qurilmalar to'g'ri,
    lekin sistematik xatolar bor; noto'g'ri so'z tanlash sezilsa-da, lug'at zaxirasi yetarli;
    tez-tez to'xtalish, takror va tuzatishlar bor, ammo ma'noni tushunish mumkin.
3 (quyi A2) - IKKITA savolga javob mavzu bo'yicha; qolgan xususiyatlar 4-balldagidek.
2 (yuqori A1) - KAMIDA IKKITA savolga javob mavzu bo'yicha; grammatika so'z va iboralar bilan
    chegaralangan, oddiy xatolar ma'noni tushunishga to'sqinlik qiladi; lug'at shaxsiy ma'lumotga
    oid juda oddiy so'zlar bilan cheklangan; to'xtalish va takrorlar tushunishga to'sqinlik qiladi.
1 (quyi A1) - FAQAT BITTA savolga javob mavzu bo'yicha; xususiyatlar 2-balldagidek.
0 - mazmunli nutq yo'q yoki barcha javoblar mavzudan butunlay tashqari (yodlangan, taxminiy)."""

_SP_12 = """5 - B1 darajadan yuqori nutq.
4 (yuqori B1) - BARCHA UCHTA savolga javob mavzu bo'yicha; oddiy grammatik qurilmalar to'g'ri,
    murakkablarini qo'llashda xatolar; lug'at topshiriq uchun yetarli, murakkab fikrda xatolar;
    ba'zi to'xtalish, takror va tuzatishlar; faqat oddiy bog'lovchilar, fikrlar bog'lanishi har doim aniq emas.
3 (quyi B1) - IKKITA savolga javob mavzu bo'yicha; qolgan xususiyatlar 4-balldagidek.
2 (yuqori A2) - KAMIDA IKKITA savolga javob mavzu bo'yicha; ba'zi oddiy grammatika to'g'ri, lekin
    sistematik xatolar; noto'g'ri so'z tanlash sezilsa-da lug'at yetarli; tez-tez to'xtalishlar;
    fikrlar bog'lanishi cheklangan, javoblar odatda punktlar ro'yxatidan iborat.
1 (quyi A2) - FAQAT BITTA savolga javob mavzu bo'yicha; xususiyatlar 2-balldagidek.
0 - nutq A2 dan past, mazmunli nutq yo'q yoki barcha javoblar mavzudan butunlay tashqari."""

_SP_2 = """(Topshiriqda 3 ta yo'naltiruvchi savol bor - "savollar" deganda shular nazarda tutiladi.)
5 - B2 darajadan yuqori nutq.
4 (yuqori B2) - BARCHA UCHTA savolga javob mavzu bo'yicha; ayrim murakkab grammatik konstruksiyalar
    to'g'ri, xatolar mazmunni tushunishga to'sqinlik qilmaydi; so'z boyligi mavzuni muhokama qilishga
    yetarli; ba'zan so'z qidirishda to'xtalishlar, lekin tushunishga qiyinchilik tug'dirmaydi;
    fikrlar bog'lanishini aniq ko'rsatish uchun bir qator bog'lovchi vositalar.
3 (quyi B2) - IKKITA savolga javob mavzu bo'yicha; qolgan xususiyatlar 4-balldagidek.
2 (yuqori B1) - KAMIDA IKKITA savolga javob mavzu bo'yicha; oddiy grammatika to'g'ri, murakkabida
    xatolar; lug'at yetarli, murakkab fikrda xatolar; ba'zi to'xtalishlar; faqat oddiy bog'lovchilar.
1 (quyi B1) - FAQAT BITTA savolga javob mavzu bo'yicha; xususiyatlar 2-balldagidek.
0 - nutq B1 dan past, mazmunli nutq yo'q yoki barcha javoblar mavzudan butunlay tashqari."""

_SP_3 = """(Topshiriq: bahsli mavzu bo'yicha yoqlovchi va qarshi fikrlar asosida taqdimot.)
6 - C1 darajadan yuqori nutq.
5 (C1) - mavzu batafsil yoritilgan, yoqlovchi/qarshi punktlar bo'yicha MUVOZANATLI dalillar;
    bir qator murakkab grammatik qurilmalar to'g'ri, kichik xatolar tushunishga to'sqinlik qilmaydi;
    lug'at yetarli; takror va tuzatishlar nutq oqimini to'xtatmaydi; xilma-xil bog'lovchilar to'g'ri.
4 (yuqori B2) - mavzuning IKKALA tomoni ham qamrab olingan; ayrim murakkab konstruksiyalar to'g'ri;
    lug'at yetarli; ba'zan so'z qidirishda to'xtalish; bir qator bog'lovchi vositalar.
3 (quyi B2) - mavzuning FAQAT YOKI ASOSAN BIR tomoni qamrab olingan; til xususiyatlari 4-balldagidek.
2 (yuqori B1) - izchil va barqaror javob yo'q, asosan topshiriqdagi punktlar TAKRORLANGAN;
    oddiy grammatika to'g'ri, murakkabida xatolar; faqat oddiy bog'lovchilar.
1 (quyi B1) - izchil javob yo'q, topshiriqdagi punktlar shunchaki O'QIB BERILGAN; xususiyatlar 2-balldagidek.
0 - nutq B1 dan past, mazmunli nutq yo'q yoki barcha javoblar mavzudan butunlay tashqari."""

SPEAKING_PARTS = {
    "1.1": {
        "name": "Part 1.1 — Shaxsiy savollar (1–3)",
        "about": "O'zingiz va kundalik hayotingiz haqida 3 ta qisqa savol.",
        "questions_hint": "3 ta qisqa shaxsiy savol qo'shing.",
        "prep": 5, "answer": 30, "max": 5,
        "labels": {5: "A2 dan yuqori", 4: "yuqori A2", 3: "quyi A2", 2: "yuqori A1", 1: "quyi A1", 0: "baholanmadi"},
        "rubric": _SP_11,
    },
    "1.2": {
        "name": "Part 1.2 — Rasm bo'yicha savollar (4–6)",
        "about": "Rasm(lar)ni tasvirlash, taqqoslash va 3 ta savolga javob.",
        "questions_hint": "Rasm bilan 3 ta savol qo'shing (rasmni 1-savolga biriktiring).",
        "prep": 10, "answer": 45, "max": 5,
        "labels": {5: "B1 dan yuqori", 4: "yuqori B1", 3: "quyi B1", 2: "yuqori A2", 1: "quyi A2", 0: "A2 dan past"},
        "rubric": _SP_12,
    },
    "2": {
        "name": "Part 2 — Monolog (7-savol)",
        "about": "Mavzu va 3 ta yo'naltiruvchi savol bo'yicha 1–2 daqiqa gapirish.",
        "questions_hint": "1 ta savol: mavzu + 3 ta yo'naltiruvchi savol bitta matnda.",
        "prep": 60, "answer": 120, "max": 5,
        "labels": {5: "B2 dan yuqori", 4: "yuqori B2", 3: "quyi B2", 2: "yuqori B1", 1: "quyi B1", 0: "B1 dan past"},
        "rubric": _SP_2,
    },
    "3": {
        "name": "Part 3 — Munozara (8-savol)",
        "about": "Bahsli mavzu: yoqlovchi va qarshi fikrlar asosida taqdimot.",
        "questions_hint": (
            "1 ta savol. Mini app'da jadval chiqishi uchun matnni shunday yozing: "
            "1-qatorda mavzu savoli, keyin «Lehine:» va uning ostida «- dalil» qatorlari, "
            "keyin «Aleyhine:» va uning ostida «- dalil» qatorlari."
        ),
        "prep": 60, "answer": 120, "max": 6,
        "labels": {6: "C1 dan yuqori", 5: "C1", 4: "yuqori B2", 3: "quyi B2", 2: "yuqori B1", 1: "quyi B1", 0: "B1 dan past"},
        "rubric": _SP_3,
    },
}

# ============================================================== WRITING

_WR_E1 = """5 (B2 yoki undan yuqori) - javob B1 darajasidan yuqori saviyada.
4 (yuqori B1) - javob mavzu doirasida; topshiriq talablarining KO'P QISMINI qamrab oladi; uslub
    talablaridan ko'pincha chetga chiqishlar; oddiy grammatika xatosiz, murakkabida xatolar;
    punktuatsiya va imlo asosan to'g'ri; so'z boyligi yetarli; oddiy bog'lovchilar bilan yaxlit matn.
3 (quyi B1) - javob QISMAN mavzu doirasida; talablarning ENG KAMIDA BIRINI qamrab oladi;
    qolgan xususiyatlar 4-balldagidek.
2 (A2) - javob qisman mavzu doirasida; talablarning faqat bir qismi; sodda gaplarda ko'p xatolar,
    ular ko'pincha tushunishga to'sqinlik qiladi; imlo xatolari sezilarli; lug'at yetarli emas;
    yaxlit matn bo'lmasligi mumkin; hajmi talabning 50% yoki undan kam bo'lishi mumkin.
1 (A1 yoki past) - A2 dan past; ona tilidan sezilarli foydalanilgan; ma'nosiz gaplar; yoki butunlay
    mavzudan tashqari (yodlangan, ko'chirilgan, taxminiy javob).
0 - javob yozilmagan."""

_WR_E2 = """5 (C1 yoki undan yuqori) - mavzuga to'liq mos; BARCHA talablar qamrab olingan; uslubga to'liq mos;
    xilma-xil murakkab grammatika, kichik xatolar tushunishga to'sqinlik qilmaydi; punktuatsiya va
    imlo xatosiz; keng qamrovli lug'at; xilma-xil va noyob bog'lovchilar ustalik bilan qo'llangan.
4 (yuqori B2) - mavzuga mos; BARCHA talablar qamrab olingan; uslubga to'liq mos; bir qancha murakkab
    grammatik strukturalar to'g'ri; kam sonli imlo xatolari; lug'at yetarli, ayrim noo'rin so'z
    tanlovlari; bir qancha bog'lovchi vositalar.
3 (quyi B2) - qisman mavzu doirasida; talablarning KO'P QISMI qamrab olingan; uslubga to'liq mos
    kelmasligi mumkin; til xususiyatlari 4-balldagidek.
2 (B1) - qisman mavzu doirasida; talablarning FAQAT BIR QISMI; uslubdan ko'pincha chetga chiqish;
    oddiy grammatika xatosiz, murakkabida xatolar; oddiy bog'lovchilar; hajmi 25-60 so'z bo'lishi mumkin.
1 (A2) - qisman mavzuda; faqat bir qismi; uslubga umuman rioya qilinmagan; ko'p xatolar tushunishga
    to'sqinlik qiladi; lug'at yetarli emas; hajmi 24 so'z yoki undan kam bo'lishi mumkin.
0 - A2 dan past; ma'nosiz gaplar; butunlay mavzudan tashqari (yodlangan, ko'chirilgan); ona tilidan
    sezilarli foydalanilgan; yoki javob yozilmagan."""

_WR_2 = """(Janr: blog posti, forum posti yoki jurnal maqolasi; publitsistik / ilmiy-ommabop uslub.)
6 (C2) - javob C1 darajasidan yuqori saviyada.
5 (C1) - o'z qarashini aniq va ravon bayon qiladi, mavzuga mos argumentlar bilan to'liq va izchil
    yoritilgan; uslubga to'liq mos; xilma-xil murakkab grammatika; imlo xatosiz; keng lug'at;
    paragraf va matn tuzish qoidalariga to'liq rioya, xilma-xil va noyob bog'lovchilar.
4 (yuqori B2) - aniq pozitsiya va mavzuga mos argumentlar, lekin ba'zan mavzudan chetga chiqish;
    uslubga to'liq mos; bir qancha murakkab grammatika to'g'ri; kam imlo xatolari; lug'at yetarli;
    mantiqiy izchillik va bog'lovchilar bor, paragraf qoidalariga rioya qilingan.
3 (quyi B2) - asosan mos, lekin o'z qarashini ochishda kamchilik YOKI mavzudan chiqish; uslubga
    to'liq mos bo'lmasligi mumkin; bog'lovchilar noaniq yoki noto'g'ri; javob to'liq tugallanmagan
    bo'lishi mumkin.
2 (B1) - asosan mos, lekin qarash ochilmagan VA mavzudan chiqishlar; uslubdan ko'pincha chetga chiqish;
    oddiy grammatika xatosiz, murakkabida xatolar; oddiy bog'lovchilar; hajmi 38-90 so'z bo'lishi mumkin.
1 (A2) - qisman mos, fikrlar aniq emas; uslubga rioya yo'q; ko'p xatolar tushunishga to'sqinlik
    qiladi; lug'at yetarli emas; hajmi 37 so'z yoki undan kam bo'lishi mumkin.
0 - A2 dan past; ma'nosiz; butunlay mavzudan tashqari; ona tilidan sezilarli foydalanilgan; yoki javob yo'q."""

WRITING_PARTS = {
    "1": {
        "name": "1-qism — Ikki elektron xat",
        "about": "Berilgan xatga javoban ikki xat: do'stga norasmiy (~50 so'z) va rahbariyatga rasmiy (120–150 so'z).",
        "words": "1-xat ~50 so'z, 2-xat 120–150 so'z",
        "components": [
            {
                "key": "email1", "name": "1-xat (norasmiy)", "words": "taxminan 50 so'z",
                "style": "norasmiy - do'stga yoki tanishga", "max": 5,
                "labels": {5: "B2 yoki yuqori", 4: "yuqori B1", 3: "quyi B1", 2: "A2", 1: "A1 yoki past", 0: "javob yo'q"},
                "rubric": _WR_E1,
            },
            {
                "key": "email2", "name": "2-xat (rasmiy)", "words": "120–150 so'z",
                "style": "rasmiy - rahbariyat, mijozlar xizmati va h.k.", "max": 5,
                "labels": {5: "C1 yoki yuqori", 4: "yuqori B2", 3: "quyi B2", 2: "B1", 1: "A2", 0: "A2 dan past"},
                "rubric": _WR_E2,
            },
        ],
    },
    "2": {
        "name": "2-qism — Blog / maqola",
        "about": "Umumiy mavzuda fikr-mulohaza: blog posti, forum posti yoki jurnal maqolasi.",
        "words": "180–200 so'z",
        "components": [
            {
                "key": "essay", "name": "Blog / maqola", "words": "180–200 so'z",
                "style": "publitsistik yoki ilmiy-ommabop", "max": 6,
                "labels": {6: "C2", 5: "C1", 4: "yuqori B2", 3: "quyi B2", 2: "B1", 1: "A2", 0: "A2 dan past"},
                "rubric": _WR_2,
            },
        ],
    },
}

# Eski (turi belgilanmagan) imtihonlar uchun - umumiy B1 mezoni bilan baholanadi
OTHER_PART = {
    "name": "Boshqa", "about": "", "questions_hint": "", "prep": 30, "answer": 60, "max": 5,
    "labels": SPEAKING_PARTS["1.2"]["labels"], "rubric": _SP_12, "words": "",
    "components": WRITING_PARTS["2"]["components"],
}


def parts_for(kind: str) -> dict:
    return WRITING_PARTS if kind == "writing" else SPEAKING_PARTS


def part_info(kind: str, part: str | None) -> dict:
    return parts_for(kind).get(part or "", OTHER_PART)