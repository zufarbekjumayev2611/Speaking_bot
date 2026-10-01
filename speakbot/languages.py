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
        "writing_example": "Your friend is planning to visit your city. Write a letter giving advice about what to see.",
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
        "writing_example": "Arkadaşınız şehrinizi ziyaret etmek istiyor. Ona neleri görmesi gerektiğini anlatan bir mektup yazın.",
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