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
    ]:
        assert contains_profanity(text), text


def test_normalization_variants():
    # Diacritics, taa marbuta, hamza forms and tatweel must all still match.
    assert contains_profanity("شَرْمُوْط")
    assert contains_profanity("شرموطة")
    assert contains_profanity("قحبة")
    assert contains_profanity("كــس أمك")


def test_english_profanity_blocked():
    for text in ["fuck you", "you bitch", "motherfucker", "asshole"]:
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
        "tez", "zeb", "neek", "a7a", "ya 5wal", "dayouth",
    ]:
        assert contains_profanity(text), text


def test_english_leet_blocked():
    for text in ["f.u.c.k", "f*ck", "sh1t", "b!tch", "@ss", "fck", "fuuuck", "stfu"]:
        assert contains_profanity(text), text


def test_strong_still_avoids_false_positives():
    for text in [
        "class and grass and glass",
        "تحقير", "تحقير الذات",
        "abdan", "ahmar", "kombucha", "document", "vacuum", "zebra",
    ]:
        assert not contains_profanity(text), text
