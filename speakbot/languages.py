"""Imtihon tili sozlamalari. Bitta kod - ikki xil bot:
.env faylida EXAM_LANGUAGE=en (ingliz tili) yoki EXAM_LANGUAGE=tr (turk tili)."""

LANGUAGES = {
    "en": {
        "name_uz": "ingliz tili",
        "speaking_name": "Speaking",
        "writing_name": "Writing",
        "lang_name": "ingliz tili (English)",
        "flag": "🇬🇧",
        "whisper_code": "en",
        # Whisper to'xtalishlarni o'chirib yubormasligi uchun namuna
        "whisper_prompt": "Um, uh... well, I mean, like, I think. Speaking exam recording.",
        "greeting": "Hello",
        "question_example": "Can you tell me about yourself? Where do you live and what do you do?",
        # Yazma topshirig'i namunalari (admin uchun, PDF tuzilishi bo'yicha)
        "wr_example_letter": (
            "You are a member of a travel club. You received this email from the club:\n\n"
            "Dear member,\n"
            "There are some changes to next month's trip to the mountains. The trip will now take place on "
            "26 May instead of 12 May. We will also travel by train instead of by bus, so the price has gone up "
            "by $20. Please tell us what you think about these changes, whether you will still join the trip "
            "and any ideas you have for the programme.\n"
            "Best regards,\nThe Club Manager"
        ),
        "wr_example_inst1": (
            "Write an email to your friend. Tell them how you feel about the changes and what you are "
            "planning to do. Write about 50 words."
        ),
        "wr_example_inst2": (
            "Write an email to the club manager. Say how you feel about the changes and what you would like "
            "the club to do. Write 120–150 words."
        ),
        "wr_example_essay": (
            "You are writing a blog post about the advantages and disadvantages of working from home. "
            "Do you think remote work will become the most common way of working in the future? Give your "
            "opinion and support your ideas with examples. Write 180–200 words."
        ),
        "special_chars": "",
        "answer_rule": "Faqat ingliz tilida, to'liq gaplar bilan javob bering.",
        "fillers": '"um", "uh", "er", "like", "you know", "I mean"',
        "simple_links": '"and", "then", "but", "so"',
        "wpm_note": "Taxminiy mo'ljal: A2 ~70-90, B1 ~90-115, B2 ~115-140, C1+ ~140+ so'z/daqiqa.",
        "grammar_focus": "zamonlar, artikllar, predloglar, fe'l shakllari, so'z tartibi",
        "mistake_types": "noto'g'ri zamon, artikl, predlog, fe'l shakli, so'z tanlash, so'z tartibi",
        "lang_adj": "Inglizcha",
        "phrases": '"In my opinion...", "For example...", "On the other hand...", "To sum up..."',
        "thinking_phrases": '"Let me think...", "That\'s an interesting question...", "Well, actually..."',
        "writing_phrases": '"Furthermore...", "However...", "As a result...", "In conclusion..."',
        "writing_style_note": (
            "Inglizcha xat qoidalari: norasmiy xat - \"Dear/Hi ...,\" va \"Best wishes\"; rasmiy xat - "
            "\"Dear Sir or Madam,\" / \"Dear Mr ...,\" va \"Yours faithfully/sincerely\". "
            "Esse - kirish, asosiy qism va xulosa."
        ),
    },
    "tr": {
        "name_uz": "turk tili",
        "speaking_name": "Konuşma",
        "writing_name": "Yazma",
        "lang_name": "turk tili (Türkçe)",
        "flag": "🇹🇷",
        "whisper_code": "tr",
        "whisper_prompt": "Iıı, şey... yani, ben, ee, şimdi düşünüyorum. Sözlü sınav kaydı.",
        "greeting": "Merhaba",
        "question_example": "Kendinizi tanıtır mısınız? Nerede yaşıyorsunuz?",
        # Yazma topshirig'i namunalari (admin uchun, PDF tuzilishi bo'yicha)
        "wr_example_letter": (
            "Bir seyahat kulübünün üyesisiniz. Kulüpten aşağıdaki e-postayı aldınız:\n\n"
            "Sevgili üyemiz,\n"
            "Gelecek ay planladığımız Kapadokya gezisinde bazı değişiklikler oldu. Gezi 12 Mayıs yerine "
            "26 Mayıs'ta yapılacak. Ayrıca otobüs yerine trenle seyahat edeceğiz, bu yüzden gezi ücreti "
            "200 lira arttı. Yeni otel ve tren bilgileri e-postanın ekinde yer alıyor. Lütfen bu değişiklikler "
            "hakkındaki düşüncelerinizi, geziye katılıp katılmayacağınızı ve gezi programı için önerilerinizi "
            "bize yazın.\n"
            "Saygılarımızla,\nKulüp Yönetimi"
        ),
        "wr_example_inst1": (
            "Bir arkadaşınıza e-posta yazın. Bu değişiklikler hakkında ne hissettiğinizi ve ne yapmayı "
            "planladığınızı anlatın. Yaklaşık 50 kelime yazın."
        ),
        "wr_example_inst2": (
            "Kulüp yöneticisine bir e-posta yazın. Değişiklikler hakkındaki düşüncelerinizi ve kulübün ne "
            "yapmasını istediğinizi açıklayın. 120–150 kelime yazın."
        ),
        "wr_example_essay": (
            "Uzaktan çalışmanın olumlu ve olumsuz yönleri hakkında bir blog yazısı yazıyorsunuz. Sizce uzaktan "
            "çalışma, geleceğin en yaygın çalışma biçimi olabilir mi? Görüşlerinizi belirtin ve fikirlerinizi "
            "örneklerle destekleyin. 180–200 kelime yazın."
        ),
        # Mini app'dagi harflar paneli (telefon klaviaturasida turkcha harflar bo'lmasa)
        "special_chars": "çğıİöşüÇĞÖŞÜ",
        "answer_rule": "Faqat turk tilida, to'liq gaplar bilan javob bering.",
        "fillers": '"ııı", "şey", "yani", "ee"',
        "simple_links": '"ve", "sonra", "ama"',
        "wpm_note": (
            "Turk tili qo'shimchali til, shuning uchun so'zlar soni kamroq bo'ladi. "
            "Taxminiy mo'ljal: A2 ~40-60, B1 ~60-85, B2 ~85-110, C1+ ~110+ so'z/daqiqa."
        ),
        "grammar_focus": (
            "hol qo'shimchalari (-i, -e, -de, -den), egalik qo'shimchalari, zamon va shaxs "
            "qo'shimchalari (-yor, -di, -miş, -ecek, -ir), unli uyg'unligi, fe'l tuslanishi, "
            "sifatdosh va ravishdoshlar (-en, -dik, -ince, -erek), so'z tartibi (fe'l oxirida)"
        ),
        "mistake_types": (
            "noto'g'ri hol yoki egalik qo'shimchasi, zamon/shaxs qo'shimchasi, unli uyg'unligi "
            "buzilishi, noto'g'ri so'z tanlash, so'z tartibi, o'zbekchadan so'zma-so'z tarjima"
        ),
        "lang_adj": "Turkcha",
        "phrases": '"Bence...", "Bana göre...", "Örneğin...", "Sonuç olarak..."',
        "thinking_phrases": '"Şöyle söyleyeyim...", "Aslında..."',
        "writing_phrases": '"Ayrıca...", "Bununla birlikte...", "Bu nedenle...", "Sonuç olarak..."',
        "writing_style_note": (
            "Turkcha xat qoidalari: norasmiy xat - \"Sevgili ...,\" / \"Merhaba ...,\" bilan boshlanib, "
            "\"Görüşmek üzere\", \"Sevgilerle\" kabi yakunlanadi; rasmiy xat - \"Sayın ...,\" bilan "
            "boshlanib, \"Saygılarımla\" bilan yakunlanadi, \"siz\" shakli va rasmiy iboralar "
            "(\"... rica ederim\", \"... bilgilerinize sunarım\") ishlatiladi. Esse - kirish, "
            "asosiy qism va \"Sonuç olarak\" bilan xulosa."
        ),
    },
}