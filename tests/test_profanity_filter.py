"""Pure unit tests for the profanity/insult filter (no DB needed)."""

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
        "متخلفه",
        "بضان",
    ]:
        assert contains_profanity(text), text


def test_normalization_variants():
    # Diacritics, taa marbuta, hamza forms and tatweel must all still match.
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
        "tez", "zeb", "neek", "a7a", "ya 5wal", "dayouth", "ent kalb",
        "ya kalb", "gahba", "kahba", "khanzir", "mnayak", "mtnak",
    ]:
        assert contains_profanity(text), text


def test_english_leet_blocked():
    for text in ["f.u.c.k", "f*ck", "sh1t", "b!tch", "@ss", "fck", "fuuuck", "stfu",
                 "n1gga", "d1ckhead", "s.h.i.t"]:
        assert contains_profanity(text), text


def test_strong_still_avoids_false_positives():
    for text in [
        "class and grass and glass",
        "تحقير", "تحقير الذات",
        "abdan", "ahmar", "kombucha", "document", "vacuum", "zebra",
        "ياكل بصل", "تحمير اللحم", "كسوف الشمس", "مخول له الصلاحية",
        "النيكوتين", "منيح", "a7mar", "a5ra", "مساقط المياه",
    ]:
        assert not contains_profanity(text), text


# --- New coverage: separator / obfuscation dodging -------------------------

def test_letter_spacing_dodge_blocked():
    for text in [
        "ك س ا م ك", "ش ر م و ط", "ن ي ك ا م ك", "ي ل ع ن د ي ن ك",
        "ك س", "a s s", "f u c k", "b i t c h", "k o s o m a k", "s h a r m o t a",
        "ن-ي-ك-ا-م-ك", "ك.س.ا.م.ك",
    ]:
        assert contains_profanity(text), text


def test_zero_width_and_persian_normalization():
    # Zero-width spaces / joiners injected between letters must not hide a slur.
    assert contains_profanity("ك\u200bس\u200bا\u200bم\u200bك")
    assert contains_profanity("ش\u200cر\u200cم\u200cو\u200cط")
    # Persian kaf/ye and Arabic-Indic digits map to their Arabic equivalents.
    assert contains_profanity("کسمک")          # Persian kaf -> ك
    assert contains_profanity("شرموط\u06cc")   # Persian ye -> ي
    assert contains_profanity("كس امك\u06f9")   # Persian digit ۹
    # Diacritics + doublings collapse.
    assert contains_profanity("شررررررمووط")
    assert contains_profanity("شررموط")


def test_franco_no_space_dodge_blocked():
    for text in ["ya7mar", "yakosomak", "yasharmota", "ya5ara", "yakhawal",
                 "kosomak", "kossomak", "5ara", "7mar", "a7a", "qahba"]:
        assert contains_profanity(text), text


def test_extended_arabic_vocabulary_blocked():
    for text in [
        "كس خالتك", "نيك اختك", "طيز امك", "يلعن شرفك", "ابن الوسخه",
        "امك في طيزي", "روح انيك نفسك", "يا عرص", "يا خول", "يا زباله",
        "يا حقير", "انيك اختك", "امك شرموطه",
    ]:
        assert contains_profanity(text), text


def test_expanded_english_franco_blocked():
    for text in ["wtf", "omfg", "dipshit", "asswipe", "kos omm", "fucktard",
                 "gahba", "kahba", "khinzeer", "mعrs"]:
        assert contains_profanity(text), text
