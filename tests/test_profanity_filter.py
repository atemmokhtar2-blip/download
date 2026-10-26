"""Pure unit tests for the profanity/insult filter (no DB needed)."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.profanity_filter import contains_profanity, normalize_profanity_text


def test_clean_messages_pass():
    assert not contains_profanity("شكراً على البوت الرائع")
    assert not contains_profanity("I love this bot, thanks")
    assert not contains_profanity("عايز أحمّل فيديو من يوتيوب")


def test_arabic_insults_blocked():
    for text in [
        "يا شرموط",
        "الشرموطة",
        "كس امك",
        "كس أمك",
        "إبن الكلب",
        "انتَ قحبه",
        "يا منيوك",
        "متناك",
        "يلعن دينك",
        "نيك امك",
        "قحاب",
        "عاهره",
        "زواني",
        "مومسه",
        "داعره",
        "ديوث",
        "قواد",
        "معرصين",
        "انت كلب",
        "يا خنزير",
        "احمق",
        "بضان",
        "نذل",
        "سافل",
    ]:
        assert contains_profanity(text), text


def test_dual_meaning_words_are_contextual():
    """Words with a normal everyday meaning only ban when directly addressed.

    A permanent ban must be justified. "متخلف" (lagging), "كلب" (dog), "حمار"
    (donkey), "زفت" (asphalt), "ساقط" (fallen), "وسخ" (dirty) … are perfectly
    innocent on their own — but "يا X" / "انت X" is an unambiguous insult.
    """
    # Innocent standalone usages stay clean (no auto-ban).
    for clean in [
        "الصوت متخلف عن الفيديو", "التحميل متخلف شوية", "بوت متخلف في السرعة",
        "عندي كلب في البيت", "الكلب حيوان أليف",
        "شفت حمار في المزرعة", "الحمار الوحشي",
        "الخنزير حيوان", "الطريق متغطي زفت",
        "الكلمة ساقطة من النص", "الملابس وسخة", "سؤال تافه",
        "عربية الزبالة", "العيش معفن", "الصوت واطي", "مستوى الصوت واطي",
        "الملابس نجسة", "دي معلومة عادية",
    ]:
        assert not contains_profanity(clean), clean

    # The SAME words addressed to someone are clear insults and still ban.
    for insult in [
        "يا كلب", "انت كلب", "يا حمار", "انت حمار", "يا خنزير", "انت متخلف",
        "يا متخلف", "انت عبيط", "يا وسخ", "انت واطي", "يا ساقط", "يا حقير",
    ]:
        assert contains_profanity(insult), insult


def test_normalization_variants():
    assert contains_profanity("شَرْمُوْط")
    assert contains_profanity("شرموطة")
    assert contains_profanity("قحبة")
    assert contains_profanity("كــس أمك")


def test_english_profanity_blocked():
    for text in ["fuck you", "you bitch", "motherfucker", "asshole",
                 "dickhead", "bullshit", "whore", "retarded", "nigger"]:
        assert contains_profanity(text), text


def test_short_terms_require_whole_word():
    # "ass" must not false-positive inside "class"/"grass"/"glass".
    assert not contains_profanity("class and grass and glass")
    assert contains_profanity("kiss my ass")


def test_normalize_is_stable():
    assert normalize_profanity_text("شَرْمُوْط") == "شرموط"
    assert normalize_profanity_text("كس-أمك") == "كس امك"


def test_franco_arabizi_blocked():
    for text in [
        "kosomak", "kosomk", "ks mk", "kos omak", "7mar", "5ara", "9a7ba",
        "qa7ba", "sharmota", "sharmoota", "khawal", "5wal", "manyak", "metnak",
        "tez", "zeb", "neek", "a7a", "ya 5wal", "dayouth", "kosom", "ksmk",
    ]:
        assert contains_profanity(text), text


def test_english_leet_blocked():
    for text in ["f.u.c.k", "f*ck", "sh1t", "b!tch", "@ss", "fck", "fuuuck", "stfu"]:
        assert contains_profanity(text), text


def test_homoglyph_dodge_blocked():
    # Cyrillic / Greek lookalike letters must collapse to ASCII.
    for text in [
        "kоsоmаk",                      # Cyrillic о + а
        "shіt",                         # Cyrillic і
        "fυck",                         # Greek υ
        "сunt",                         # Cyrillic с
        "fүсk",                         # Cyrillic у + с
        "bіtсh",                        # Cyrillic і + с
        "kіs оmаk",                     # mixed Cyrillic
    ]:
        assert contains_profanity(text), text


def test_fuzzy_typo_blocked():
    # A single substituted/transposed letter in a long slur still matches.
    for text in [
        "kosmok", "sharmootah", "shermouta", "manyek", "maneek", "metnqk",
        "khanzer", "dyouth", "sharmutta", "kosemak",
    ]:
        assert contains_profanity(text), text


def test_letter_spacing_and_separator_dodge_blocked():
    for text in [
        "ك س ا م ك", "ش ر م و ط", "ن ي ك ا م ك",
        "k o s o m a k", "s h a r m o t a",
        "ك.س.ا.م.ك", "ك-س-ا-م-ك", "f u c k",
    ]:
        assert contains_profanity(text), text


def test_zero_width_and_persian_normalization():
    assert contains_profanity("کسمک")            # Persian kaf كسمك
    assert contains_profanity("شرموط\u06cc")     # Persian ye شرموطي
    assert contains_profanity("كس امك\u06f9")     # Persian digit ۹
    assert contains_profanity("كس\u200bام\u200bك")  # zero-width space
    assert contains_profanity("شررررررمووط")
    assert contains_profanity("شررموط")


def test_expanded_english_franco_blocked():
    for text in ["wtf", "omfg", "dipshit", "asswipe", "kos omm", "fucktard",
                 "gahba", "kahba", "khinzeer", "mعrs"]:
        assert contains_profanity(text), text


def test_strong_still_avoids_false_positives():
    for text in [
        # English short-word protections under no fuzzy
        "class and grass and glass", "shut the door", "a batch of files",
        "this ship", "cosmos", "maniac", "kismet",
        # Arabic short-word / substring protections
        "تحقير", "تحقير الذات", "كسوف الشمس", "مخول له الصلاحية",
        "النيكوتين", "منيح", "ياكل بصل", "تحمير اللحم", "مساقط المياه",
        "عاهده", "معاهده",
        # Franco clean lookalikes
        "abdan", "ahmar", "a7mar", "a5ra", "kombucha", "document", "vacuum",
        "zebra",
    ]:
        assert not contains_profanity(text), text
