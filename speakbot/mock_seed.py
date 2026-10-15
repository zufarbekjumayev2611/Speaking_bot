"""Boshlang'ich 5 ta namunaviy full mock (bot birinchi marta ishga tushganda bir marta yaratiladi).

Har bir mock: Konuşma Part 1.1 (3 savol), 1.2 (3 savol), 2 (3 yo'naltiruvchi savol), 3 (munozara) va to'liq
Yazma (kelgan xat, 2 ta xat ko'rsatmasi, blog topshirig'i). Part 1.2 va 2 rasmsiz - admin «📚 Mocklar» dan
rasm qo'shishi mumkin. O'chirilgan namunalar qayta yaratilmaydi (sozlamada belgi qoladi)."""
import json
import logging

import db
import writing

log = logging.getLogger("mock_seed")
SEEDED_KEY = "mocks_seeded"

MOCKS = [
    {
        "1.1": [
            "Kendinizi kısaca tanıtır mısınız? Nerede yaşıyorsunuz?",
            "Boş zamanlarınızda en çok ne yapmaktan hoşlanırsınız?",
            "En sevdiğiniz mevsim hangisi ve neden?",
        ],
        "1.2": [
            "Kalabalık bir şehir merkezi ile sessiz bir köyü karşılaştırın. Benzerlikleri ve farkları nelerdir?",
            "Siz hangisinde yaşamayı tercih edersiniz? Neden?",
            "Sizce gelecekte insanlar şehirlerden köylere taşınacak mı?",
        ],
        "2": [
            "Hayatınızda sizi en çok etkileyen öğretmen kimdi?",
            "Bu öğretmen size neler öğretti?",
            "Sizce iyi bir öğretmenin en önemli özelliği nedir?",
        ],
        "3": (
            "Öğrenciler okulda üniforma giymeli mi?",
            ["Öğrenciler arasındaki ekonomik farkları gizler.", "Okulda disiplin ve düzen sağlar.",
             "Sabahları ne giyeceğini düşünme zamanını azaltır."],
            ["Öğrencilerin kişisel tarzını ifade etmesini engeller.", "Aileler için ek bir masraftır.",
             "Rahat olmayan kıyafetler derse odaklanmayı zorlaştırabilir."],
        ),
        "writing": {
            "letter": ("Bir dil kursuna kayıtlısınız. Kurs müdürlüğünden şu e-postayı aldınız:\n"
                       "\"Değerli öğrencimiz, gelecek aydan itibaren akşam derslerimiz hafta sonuna taşınacaktır. "
                       "Ayrıca ders ücretleri yüzde yirmi artacaktır. Yeni programla ilgili görüş ve önerilerinizi "
                       "bize yazmanızı rica ederiz. Saygılarımızla, Kurs Müdürlüğü\""),
            "inst1": "Aynı kursa giden bir arkadaşınıza e-posta yazın: bu değişiklikler hakkında ne hissediyorsunuz ve ne yapmayı planlıyorsunuz?",
            "inst2": "Kurs müdürlüğüne resmî bir e-posta yazın: değişikliklerle ilgili sorunları anlatın ve bir çözüm önerin.",
            "essay": ("Bir eğitim blogu için yazı yazıyorsunuz. Konu: \"Yabancı dil öğrenmek için yurt dışına gitmek "
                      "şart mı?\" Görüşünüzü açıklayın, gerekçeler ve örnekler verin. (180–200 kelime)"),
        },
    },
    {
        "1.1": [
            "Ailenizden bahseder misiniz? Kaç kişisiniz?",
            "Sabahları genellikle ne yaparsınız?",
            "En sevdiğiniz yemek nedir ve onu kim yapar?",
        ],
        "1.2": [
            "Evde yemek yapmak ile restoranda yemek yemeyi karşılaştırın. Benzerlikleri ve farkları nelerdir?",
            "Siz hangisini daha çok tercih edersiniz? Neden?",
            "Sizce gençler neden fast food'u seviyor?",
        ],
        "2": [
            "Unutamadığınız bir yolculuğunuzu anlatın. Nereye gittiniz?",
            "Bu yolculukta neler yaşadınız?",
            "Bu yolculuk size ne kazandırdı?",
        ],
        "3": (
            "Çocuklar akıllı telefon kullanmalı mı?",
            ["Acil durumlarda aileleriyle iletişim kurabilirler.", "Eğitim uygulamalarıyla yeni bilgiler öğrenebilirler.",
             "Teknolojiyi erken yaşta öğrenmek gelecekte avantaj sağlar."],
            ["Ekran bağımlılığı riskini artırır.", "Fiziksel aktiviteyi ve oyun zamanını azaltır.",
             "İnternette uygunsuz içeriklere ulaşabilirler."],
        ),
        "writing": {
            "letter": ("Bir spor salonuna üyesiniz. Salon yönetiminden şu e-postayı aldınız:\n"
                       "\"Sayın üyemiz, bakım çalışmaları nedeniyle havuzumuz iki ay kapalı kalacaktır. Bu süre içinde "
                       "üyelik ücretlerinde değişiklik yapılmayacaktır. Yeni grup derslerimiz için hangi saatlerin size "
                       "uygun olduğunu bildirmenizi rica ederiz. Spor Salonu Yönetimi\""),
            "inst1": "Birlikte spor yaptığınız bir arkadaşınıza e-posta yazın: bu haber hakkında ne düşünüyorsunuz ve bu iki ay ne yapmayı öneriyorsunuz?",
            "inst2": "Salon yönetimine resmî bir e-posta yazın: kararla ilgili düşüncelerinizi belirtin, indirim veya başka bir çözüm talep edin.",
            "essay": ("Bir sağlık dergisinin forumu için yazıyorsunuz. Konu: \"Spor yapmak için spor salonuna gitmek "
                      "gerekli mi, yoksa evde de olur mu?\" Görüşünüzü gerekçeleriyle yazın. (180–200 kelime)"),
        },
    },
    {
        "1.1": [
            "Hangi şehirde doğdunuz? Orayı biraz anlatır mısınız?",
            "Hafta sonlarını genellikle nasıl geçirirsiniz?",
            "Kitap okumayı sever misiniz? En son hangi kitabı okudunuz?",
        ],
        "1.2": [
            "Toplu taşıma ile kendi arabanızla yolculuk etmeyi karşılaştırın. Benzerlikleri ve farkları nelerdir?",
            "Şehrinizde ulaşım sorunu var mı? Nasıl çözülebilir?",
            "Sizce gelecekte insanlar nasıl seyahat edecek?",
        ],
        "2": [
            "Öğrenmek istediğiniz bir beceri veya hobi hangisi?",
            "Bu beceriyi neden öğrenmek istiyorsunuz?",
            "Bu beceriyi öğrenmek için neler yapmayı planlıyorsunuz?",
        ],
        "3": (
            "Uzaktan (online) eğitim, yüz yüze eğitimin yerini almalı mı?",
            ["Zaman ve yol masrafından tasarruf sağlar.", "Öğrenciler kendi hızlarında öğrenebilir.",
             "Uzak bölgelerdeki öğrenciler de kaliteli eğitime ulaşabilir."],
            ["Öğretmenle ve arkadaşlarla iletişim zayıflar.", "Herkesin iyi internet bağlantısı yoktur.",
             "Evde dikkat dağıtan şeyler çoktur, motivasyon düşer."],
        ),
        "writing": {
            "letter": ("Mahallenizde yeni bir kütüphane açıldı. Kütüphane yönetiminden şu e-postayı aldınız:\n"
                       "\"Değerli okurumuz, kütüphanemiz hafta içi 09.00–17.00 saatleri arasında açık olacaktır. "
                       "Kitap ödünç alma süresi bir haftadır. Hangi etkinlikleri düzenlememizi istediğinizi bize "
                       "yazabilirsiniz. Kütüphane Yönetimi\""),
            "inst1": "Kitap okumayı seven bir arkadaşınıza e-posta yazın: yeni kütüphane hakkında ne düşündüğünüzü anlatın ve onu birlikte gitmeye davet edin.",
            "inst2": "Kütüphane yönetimine resmî bir e-posta yazın: çalışma saatleri ve ödünç alma süresiyle ilgili sorunları belirtin, önerilerde bulunun.",
            "essay": ("Bir kültür blogu için yazı yazıyorsunuz. Konu: \"Dijital çağda kâğıt kitaplar hâlâ gerekli mi?\" "
                      "Görüşünüzü açıklayın, örnekler verin. (180–200 kelime)"),
        },
    },
    {
        "1.1": [
            "Şu anda okuyor musunuz, yoksa çalışıyor musunuz? Biraz anlatır mısınız?",
            "En yakın arkadaşınızı tanıtır mısınız?",
            "Hangi tür müzikleri dinlemeyi seversiniz?",
        ],
        "1.2": [
            "Büyük bir alışveriş merkezi ile geleneksel bir pazarı karşılaştırın. Benzerlikleri ve farkları nelerdir?",
            "Siz alışverişi nereden yapmayı tercih edersiniz? Neden?",
            "İnternetten alışverişin avantajları ve dezavantajları nelerdir?",
        ],
        "2": [
            "Gelecekte hangi meslekte çalışmak istiyorsunuz?",
            "Bu mesleği neden seçtiniz?",
            "Bu meslek için hangi bilgi ve becerilere ihtiyacınız var?",
        ],
        "3": (
            "Gençler üniversiteden sonra hemen çalışmaya başlamalı mı?",
            ["Erken iş tecrübesi kariyerde avantaj sağlar.", "Ekonomik bağımsızlık kazanırlar.",
             "Okulda öğrendiklerini pratikte kullanırlar."],
            ["Yüksek lisans ile daha iyi işler bulabilirler.", "Seyahat ederek dünyayı tanıma fırsatını kaçırırlar.",
             "Doğru mesleği seçmek için zamana ihtiyaçları vardır."],
        ),
        "writing": {
            "letter": ("Bir yaz kampına katılmak için başvurdunuz. Kamp organizatöründen şu e-postayı aldınız:\n"
                       "\"Sevgili katılımcımız, kampımız bu yıl şehir dışında, bir göl kenarında yapılacaktır. "
                       "Konaklama çadırlarda olacak ve programda doğa yürüyüşleri yer alacaktır. Özel bir isteğiniz "
                       "varsa bize bildiriniz. Kamp Organizasyon Ekibi\""),
            "inst1": "Kampa sizinle gelecek bir arkadaşınıza e-posta yazın: kamp hakkında neler hissettiğinizi ve hazırlık planlarınızı anlatın.",
            "inst2": "Kamp organizatörüne resmî bir e-posta yazın: konaklama ve program hakkında sorular sorun, özel bir isteğinizi belirtin.",
            "essay": ("Bir gençlik forumunda yazıyorsunuz. Konu: \"Gençler yaz tatilini çalışarak mı, dinlenerek mi "
                      "geçirmeli?\" Görüşünüzü gerekçeleriyle açıklayın. (180–200 kelime)"),
        },
    },
    {
        "1.1": [
            "Evinizi veya odanızı tarif eder misiniz?",
            "Günde ne kadar süre internet kullanıyorsunuz?",
            "Bayramlarda veya tatillerde neler yaparsınız?",
        ],
        "1.2": [
            "Evde çalışmak ile ofiste çalışmayı karşılaştırın. Benzerlikleri ve farkları nelerdir?",
            "Siz hangisini tercih ederdiniz? Neden?",
            "Sizce gelecekte ofisler tamamen ortadan kalkacak mı?",
        ],
        "2": [
            "Çevre kirliliği yaşadığınız yerde nasıl bir sorun?",
            "Bu sorunun nedenleri nelerdir?",
            "Bu sorunu çözmek için insanlar ve devlet neler yapabilir?",
        ],
        "3": (
            "Şehir merkezlerinde özel araç kullanımı yasaklanmalı mı?",
            ["Hava kirliliği ve gürültü azalır.", "Trafik sıkışıklığı ortadan kalkar.",
             "İnsanlar daha çok yürür ve bisiklet kullanır."],
            ["Yaşlılar ve engelliler için ulaşım zorlaşır.", "Şehir merkezindeki dükkânlar müşteri kaybeder.",
             "Toplu taşıma herkes için yeterli değildir."],
        ),
        "writing": {
            "letter": ("Bir apartmanda oturuyorsunuz. Apartman yönetiminden şu e-postayı aldınız:\n"
                       "\"Sayın komşumuz, gelecek ay bahçede park yeri yapılacak ve çocuk oyun alanı kaldırılacaktır. "
                       "Ayrıca aidatlar artacaktır. Bu konudaki düşüncelerinizi toplantıdan önce bize iletmenizi rica "
                       "ederiz. Apartman Yönetimi\""),
            "inst1": "Aynı apartmanda oturan bir arkadaşınıza e-posta yazın: bu karar hakkında ne hissettiğinizi ve ne yapmak istediğinizi anlatın.",
            "inst2": "Apartman yönetimine resmî bir e-posta yazın: karara itiraz edin, nedenlerini açıklayın ve alternatif bir çözüm önerin.",
            "essay": ("Bir şehir dergisinin blogu için yazıyorsunuz. Konu: \"Şehirlerde daha çok park mı, daha çok "
                      "otopark mı gerekli?\" Görüşünüzü gerekçeler ve örneklerle açıklayın. (180–200 kelime)"),
        },
    },
]

P12_TIMES = [45, 30, 30]


def _part3_text(title: str, pros: list[str], cons: list[str]) -> str:
    return "\n".join([title, "Lehine:"] + [f"- {x}" for x in pros] + ["Aleyhine:"] + [f"- {x}" for x in cons])


async def fill_mock(mock_id: int, data: dict):
    exams = {(ex.get("kind") or "speaking", ex.get("part")): ex["id"] for ex in await db.mock_exams(mock_id)}
    sql = ("INSERT INTO questions (exam_id, position, text, photo_file_id, prep_sec, answer_sec, meta) "
           "VALUES (?, ?, ?, NULL, ?, ?, NULL)")
    rows = []
    for i, q in enumerate(data["1.1"], 1):
        rows.append((sql, (exams[("speaking", "1.1")], i, q, 5, 30)))
    for i, q in enumerate(data["1.2"], 1):
        rows.append((sql, (exams[("speaking", "1.2")], i, q, 10, P12_TIMES[i - 1])))
    rows.append((sql, (exams[("speaking", "2")], 1, "\n".join(f"• {q}" for q in data["2"]), 60, 120)))
    rows.append((sql, (exams[("speaking", "3")], 1, _part3_text(*data["3"]), 60, 120)))
    values = data["writing"]
    rows.append(("INSERT INTO questions (exam_id, position, text, photo_file_id, prep_sec, answer_sec, meta) "
                 "VALUES (?, 1, ?, NULL, 0, 0, ?)",
                 (exams[("writing", "full")], writing.compose("full", values), json.dumps(values, ensure_ascii=False))))
    await db._execute_many(rows)


async def ensure_seed() -> int:
    """Mock hali umuman yo'q bo'lsa - 5 ta namunaviy mock yaratadi (bir marta). Nechta yaratilganini qaytaradi."""
    if db.setting(SEEDED_KEY):
        return 0
    created = 0
    if await db.count_mocks() == 0:
        for data in MOCKS:
            mock_id = await db.create_mock()
            await fill_mock(mock_id, data)
            created += 1
        log.info("%d ta namunaviy mock yaratildi", created)
    await db.set_setting(SEEDED_KEY, "1")
    return created
