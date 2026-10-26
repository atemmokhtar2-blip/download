"""Strong profanity / insult filter for outbound "message to admin" content.

Design — every obfuscation trick is handled by *normalization* first, then by
layered *matching*, so the dictionaries stay readable while coverage is wide.

Normalization layers
  1. NFKC (full-width/half-width), zero-width chars removed, Arabic-Indic /
     Persian digits -> ASCII, Persian kaf/ye -> Arabic, casefold.
  2. Arabic diacritics + tatweel removed.
  3. Arabic letter-shape variants collapsed to one canonical form
     (أ/إ/آ -> ا, ة -> ه, ى -> ي, ؤ -> و, ئ -> ي).
  4. English leet deobfuscation (@/4->a, 1/!->i, 0->o, 3->e, 5/$->s, 7->t, 8->b,
     9->g) and Franco/Arabizi digits -> Arabic letters (2->ا 3->ع 5->خ 6->ط
     7->ح 8->غ 9->ق).
  5. Repeats collapsed ("شششمووط" -> "شموط", "fuuuck" -> "fuck").

Matching layers
  * PHRASES — multi-word curses; matched as *compact* substrings so both
    "كس امك" and the spaced/period-dodged "ك س ا م ك" collapse to "كسامك".
  * STRONG  — longer unambiguous slurs; matched as compact substrings anywhere
    ("الشرموطة", "ياشرموط", "sharmoota" all contain the slur).
  * EXACT   — short/ambiguous terms; matched ONLY as whole tokens (space-split)
    or as an exact match of the whole compact text, so "كس" never
    false-positives inside "كسوف" and "ass" never fires inside "class".

The exact/strong split is what keeps the filter precise: a word like "نيك",
"حقير", "خول" or "كلب" can appear inside innocent Arabic words ("تحقير",
"مخول", "مونيكا") and therefore stays in EXACT, while "شرموط", "قحبه",
"خنزير", "حمار" are unambiguous enough for substring matching.
"""

from __future__ import annotations

import re
import unicodedata

# ---------------------------------------------------------------------------
# Normalization primitives
# ---------------------------------------------------------------------------

# Arabic diacritics + tatweel (ـ) and the kashida/zero-width joiners.
_DIACRITICS = re.compile(r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED\u0640]")
_ZERO_WIDTH = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff\u00ad\u180e]")

# Non-word characters (everything except \w and Arabic letters) become a single
# space, so "كس-أمك" -> "كس امك" and "كس 😡 أمك" -> "كس امك".
_NON_WORD = re.compile(r"[^\w\u0621-\u064A]", re.UNICODE)

# Arabic-Indic (٠-٩), Extended Arabic-Indic / Persian (۰-۹) -> ASCII digits.
_DIGITS_TO_ASCII = str.maketrans({
    **{chr(0x0660 + i): str(i) for i in range(10)},
    **{chr(0x06F0 + i): str(i) for i in range(10)},
})

# Persian / non-Arabic letter-shape variants -> Arabic.
_PERSIAN_TO_ARABIC = str.maketrans({
    "\u06A9": "\u0643",  # ک  -> ك
    "\u06AA": "\u0643",  # ڪ  -> ك
    "\u06CC": "\u064A",  # ی  -> ي
    "\u06C0": "\u0647",  # ۀ  -> ه
    "\u06BE": "\u0647",  # ھ  -> ه
    "\u06C1": "\u0647",  # ہ  -> ه
    "\u06C2": "\u0647",  # ۂ  -> ه
    "\u06D5": "\u0647",  # ە  -> ه
})

# Arabic letter-shape variants -> one canonical form.
_ARABIC_TRANSLIT = str.maketrans({
    "\u0649": "\u064A",  # ى  -> ي  (alef maqsura)
    "\u0629": "\u0647",  # ة  -> ه  (taa marbuta)
    "\u0623": "\u0627",  # أ  -> ا
    "\u0625": "\u0627",  # إ  -> ا
    "\u0622": "\u0627",  # آ  -> ا
    "\u0624": "\u0648",  # ؤ  -> و
    "\u0626": "\u064A",  # ئ  -> ي
})

# English leet substitutions.
_LEET = str.maketrans({
    "@": "a", "4": "a", "$": "s", "5": "s", "1": "i", "!": "i",
    "0": "o", "3": "e", "7": "t", "9": "g", "8": "b",
})

# Franco-Arabic digits -> Arabic letters (Arabizi).
_FRANCO_DIGITS = str.maketrans({
    "2": "ا", "3": "ع", "5": "خ", "6": "ط", "7": "ح", "8": "غ", "9": "ق",
})

# Compact forms: strip everything except the relevant alphabet so "f.u.c.k" ->
# "fuck", "كس-أمك" -> "كسأمك"->"كسامك" after space removal, etc.
_LATIN_COMPACT = re.compile(r"[^a-z0-9]")
_ARABIC_COMPACT = re.compile(r"[^\u0621-\u064A0-9]")
_FRANCO_COMPACT = re.compile(r"[^a-z0-9\u0621-\u064A]")

# Collapse repeats: Arabic/Franco doubling is just emphasis ("شررموط" ->
# "شرموط", "teeeeez" -> "tez"); English needs 3+ to stay safe with words like
# "good"/"matter" ("fuuuck" -> "fuck").
_REPEAT2 = re.compile(r"(.)\1+")
_REPEAT3 = re.compile(r"(.)\1{2,}")


# ---------------------------------------------------------------------------
# Text preparation
# ---------------------------------------------------------------------------

def _prepare(text: str) -> str:
    """One shared canonical base used by every script matcher."""
    t = unicodedata.normalize("NFKC", str(text or ""))
    t = _ZERO_WIDTH.sub("", t)
    t = t.translate(_DIGITS_TO_ASCII)
    t = t.translate(_PERSIAN_TO_ARABIC)
    t = t.casefold()
    t = _DIACRITICS.sub("", t)
    t = t.translate(_ARABIC_TRANSLIT)
    return t


def _arabic_spaced(t: str) -> str:
    x = _NON_WORD.sub(" ", t)
    x = _REPEAT2.sub(r"\1", x)
    return re.sub(r"\s+", " ", x).strip()


def _arabic_compact(t: str) -> str:
    x = _ARABIC_COMPACT.sub("", t)
    x = _REPEAT2.sub(r"\1", x)
    return x


def _english_spaced(t: str) -> str:
    x = t.translate(_LEET)
    x = _NON_WORD.sub(" ", x)
    x = _REPEAT3.sub(r"\1", x)
    return re.sub(r"\s+", " ", x).strip()


def _english_compact(t: str) -> str:
    x = t.translate(_LEET)
    x = _LATIN_COMPACT.sub("", x)
    x = _REPEAT3.sub(r"\1", x)
    return x


def _franco_spaced(t: str) -> str:
    x = t.translate(_FRANCO_DIGITS)
    x = _NON_WORD.sub(" ", x)
    x = _REPEAT2.sub(r"\1", x)
    return re.sub(r"\s+", " ", x).strip()


def _franco_compact(t: str) -> str:
    x = t.translate(_FRANCO_DIGITS)
    x = _FRANCO_COMPACT.sub("", x)
    x = _REPEAT2.sub(r"\1", x)
    return x


def normalize_profanity_text(text: str) -> str:
    """Return the canonical Arabic-script comparison form (public API)."""
    return _arabic_spaced(_prepare(text))


# ---------------------------------------------------------------------------
# Arabic lists (canonical ا / ي / ه, no diacritics)
# ---------------------------------------------------------------------------

# Multi-word curses. The matcher strips separators from BOTH the phrase and the
# text, so "كس امك", "كس-أمك", "ك س ا م ك" all resolve to "كسامك". "يا X" and
# "انت X" are deliberately NOT here — the standalone noun in those phrases is
# already a token (or STRONG substring), and a "يا" compact phrase would
# false-positive on innocent merges like "ياكل بصل" (he eats onion).
_AR_PHRASES = [
    # كس / كسم / نيك / طيز / زب + a relative (the compact forms stop the
    # no-space dodge "ك س ا م ك"; the spaced forms are caught by EXACT tokens).
    "كس امك", "كس ام", "كس امه", "كس اختك", "كس اخت", "كس اخته",
    "كس ابوك", "كس ابوه", "كس اخوك", "كس خواتك", "كس خالتك", "كس عمتك",
    "كس جدتك", "كس مراتك", "كس حبيبتك", "كس خطيبتك", "كس عيلتك", "كس عيلتكم",
    "كسمك", "كسم امك", "كسم ام", "كسمه", "كسم اختك", "كسم ابوك", "كسم خواتك",
    "كسم عيلتك", "كسم دينك", "كسم ربك", "كسم ربنا", "كسم الدين", "كسم امه",
    "نيك امك", "نيك ام", "نيك اختك", "نيك اخته", "نيك ابوك", "نيك خواتك",
    "نيك امه", "نيج امك", "نيج اختك",
    "طيز امك", "طيز اختك", "طيز ابوك", "طيز اخته", "طيز خواتك",
    "زب ابوك", "زب امك", "اير ابوك", "اير امك",
    # لعن
    "يلعن دينك", "يلعن ابوك", "يلعن ابوه", "يلعن امك", "يلعن امه",
    "يلعن ربك", "يلعن ربنا", "يلعن جدك", "يلعن خواتك", "يلعن اختك",
    "يلعن عرضك", "يلعن شرفك", "يلعن دين", "يلعن دينه",
    "الله يلعنك", "ربنا يلعنك", "الله يلعن", "يلعن ام", "يلعن ابو",
    "يخرب بيتك", "الله يخرب بيتك", "يخرب بيت", "يخرب بيتكم", "يخرب دينك",
    "عليك اللعنه", "اللعنه عليك", "عليك لعنه", "لعنه عليك",
    # ابن / بنت + نعت
    "ابن الكلب", "ابن الكلبه", "ابن كلب", "بنت الكلب", "بنت كلب",
    "ابن القحبه", "ابن الشرموطه", "ابن العاهره", "ابن الزانيه",
    "ابن المتناكه", "ابن المنتاكه", "ابن المنيوكه", "ابن الوسخه",
    "ابن الكحبه", "ابن الديوث", "ابن العرص", "ابن المعرص", "ابن النايك",
    "ابن الناك", "ابن الخول", "ابن المنيوك", "ابن المنتاك",
    "بنت القحبه", "بنت الشرموطه", "بنت العاهره", "بنت الزانيه",
    "بنت المتناكه", "بنت المنيوكه",
    # تهديد / سبّ صريح
    "روح انيك", "روح نيك", "انيك نفسك", "روح انيك نفسك", "انا انيكك",
    "انيك امك", "انيك اختك", "على كسمك", "امك في طيزي", "اختك في طيزي",
]

# Long, unambiguous slurs — matched as substrings in the compact form (which
# also drops separators), so "الشرموطة", "يا شرموط", "ش ر م و ط" all match.
_AR_STRONG = [
    # جنسية
    "شرموط", "شرموطه", "شراميط", "شراميت",
    "قحبه", "قحاب", "كحبه", "كحاب",
    "عاهره", "عواهر", "عاهر",
    "زانيه", "زواني", "داعره", "داعر", "مومس", "مومسه", "مومسات",
    "منيوك", "منيوكه", "منيوكين", "منيوج", "منيوجه", "منيوجين",
    "متناك", "متناكه", "متناكين", "متناكات",
    "منتاك", "منتاكه", "منتاكين",
    "منيك", "منيكه", "منيكين",
    "نايك", "نايكه", "ناكت", "نياك", "نياكه",
    "ينيك", "تنيك", "انيك", "انيكك", "انيكي", "انيكها", "نيكها", "نيجها",
    "بنيك", "بنكك", "بنيكك",
    "ديوث", "دياثه", "قواد", "قواده", "معرص", "معرصه",
    # أعضاء (الطويلة غير الملبسة)
    "طيزه", "طيزك", "طياز", "طيوز",
    # إهانات
    "حمار", "خنزير", "خنازير",
    "احمق", "معتوه", "معتوهه", "متخلف", "متخلفه", "عبيط", "عبيطه",
    "مقرف", "مقرفه", "منحط", "منحطه", "بضان", "بظين",
]

# Short / ambiguous terms — whole-token only (plus exact compact match for the
# letter-spacing dodge "ك س"). These are the words that MUST NOT be substring
# matched: "تحقير" contains "حقير", "مخول" contains "خول", "كسوف" contains
# "كس", "مونيكا" contains "نيك".
_AR_EXACT = {
    "زب", "زبي", "زبك", "زبر", "زبره", "اير", "ايري", "ايرك", "عير", "عيري",
    "كس", "كسي", "كسم", "كسمك", "كسمه",
    "نيك", "نيج", "نيجه", "احا", "احه",
    "عرص", "خول", "خوال", "خولات", "خوله",
    "كلب", "كلاب", "كلبه", "طيز", "طيزي",
    "غبي", "غبيه", "اهبل", "ابله", "هبله", "هبيل", "بليد", "بليده",
    "وسخ", "وسخه", "نجس", "نجسه", "قذر", "قذره", "حقير", "حقيره",
    "زباله", "تافه", "تافهه", "جبان", "جبانه", "تفو", "زفت", "خرا", "خره",
    "خراء", "ساقط", "ساقطه", "حمير",
}


# ---------------------------------------------------------------------------
# English lists
# ---------------------------------------------------------------------------

_EN_PHRASES = [
    "fuck you", "fuck u", "fuck ur", "fuck your", "fuck yourself", "fuck urself",
    "fuck off", "fuck off you", "fucking you", "fucking your",
    "mother fucker", "mutha fucka", "son of a bitch", "son of bitch",
    "son of a whore", "piece of shit", "piece of crap",
    "kiss my ass", "kiss my arse", "suck my", "suck a", "suck my dick",
    "suck my cock", "suck it", "you suck", "u suck", "blow me", "eat shit",
    "eat a dick", "go to hell", "go fuck", "go fuck yourself",
    "shut the fuck", "what the fuck", "shut up bitch", "stfu", "gtfo", "wtf",
    "omfg", "fuck you all", "fuck this", "fuck that", "fuck your mom",
    "fuck your mother", "fuck your dad", "you fucker", "you shit",
    "you bastard", "you bitch", "you cunt", "you whore", "you slut",
    "you asshole", "you piece of shit", "fucked up", "fuck sake",
    "for fucks sake",
]

_EN_STRONG = [
    "fuck", "fck", "fcuk", "fock", "fuk", "fuq",
    "fucking", "fuckin", "fucker", "fuckers", "fucked",
    "motherfucker", "motherfuckers", "motherfucking", "muthafucka",
    "muthafuckas", "mothafucka", "muthafuckin", "mofucka",
    "shit", "bullshit", "shithead", "shitheads", "shitface", "dipshit",
    "dipshits", "shitty", "shitbag", "shite",
    "bitch", "bitches", "bitching", "bitchy", "biatch", "beatch", "beotch",
    "beyotch", "bich",
    "asshole", "assholes", "asswipe", "asshat", "assclown", "dumbass",
    "dumbasses", "jackass", "jackasses", "bastard", "bastards",
    "dickhead", "dickheads", "cockhead", "faggot", "faggots", "fagot",
    "nigger", "nigga", "cocksucker", "cocksuckers", "cocksucking",
    "douche", "douchebag", "douchebags", "douchbag",
    "retarded", "retard", "retards", "whore", "whores", "slut", "sluts",
    "slutty", "cumslut", "cumbucket", "porn", "porno",
    "dumbfuck", "fucktard", "fuckwit", "fuckface",
]

_EN_EXACT = {
    "ass", "arse", "dick", "dicks", "cock", "cocks", "cunt", "cunts",
    "cum", "cums", "jizz", "jism", "pussy", "pussies", "hoe", "hoes",
    "thot", "thots", "fag", "fags", "prick", "twat", "twats", "wanker",
    "wankers", "bollocks", "bollock", "bellend", "nonce", "fuk", "fuq",
    "sht", "dik",
}


# ---------------------------------------------------------------------------
# Franco / Arabizi lists (digits already mapped to ع/خ/ط/ح/غ/ق)
# ---------------------------------------------------------------------------

_FRANCO_PHRASES = [
    "kos omak", "kos omk", "kos emak", "kos emk", "kus omak", "kus omk",
    "kus emak", "kus emk", "kis omak", "kis omk", "kos ommak", "kos om",
    "kos omm", "kos mk", "kus mk", "kis mk", "kos abok", "kos abouk",
    "kus abuk", "ya 7mar", "ya 5ara", "ya khara", "ya kalb", "ya khawal",
    "ya 5wal", "ya qahba", "ya gahba", "ya sharmota", "ya manyak",
    "ya metnak", "ya zeb", "ya tez", "ya neek", "ya ars", "ya dayouth",
    "ent kalb", "enta kalb", "ent 7mar", "enta 7mar", "ent khara",
    "enta khara", "ent 5ara", "enta 5ara", "ent zeb", "enta zeb",
    "ent 5wal", "enta khawal", "ent kosomak", "enta kosomk",
]

_FRANCO_STRONG = [
    # كسمك / كس
    "kosom", "kusom", "ksom", "kossom", "kossm", "kosomk", "kusomk",
    "ksomk", "kossmk", "kosomak", "kusomak", "kosmak", "kosmk", "kusmk",
    "ksmk", "kossomak",
    # شرموطة
    "sharmota", "sharmuta", "sharmoota", "sharmouta", "sharmout",
    "shermota", "shermuta", "shermoota", "shermouta", "charmota",
    "charmuta", "charmouta", "charmoota", "sharmot", "sharmut",
    "sharmoطa", "sharmuطa", "sharmوطa", "shermوطa", "sharmute", "sharmote",
    # قحبة
    "qahba", "gahba", "kahba", "qaحba", "قaحba", "gaحba", "kaحba",
    "qahb", "gahb", "kahb", "قaحb", "qaحb", "gaحb", "kaحb",
    "qahbah", "gahbah", "kahbah",
    # خول
    "khawal", "khwal", "khawl", "khol", "khuwal", "khowal",
    "خawal", "خwal", "خwl", "خol",
    # خنزير
    "khanzir", "khinzir", "khanzer", "khinzer", "khanzeer", "خanzir", "خnzir",
    # منيوك / متناك
    "menyak", "manyak", "mnayak", "mnayek", "menyok", "mnayk", "mnyk",
    "menyuk", "monyak", "munyak", "mniak", "mneek",
    "metnak", "matnak", "mtnak", "metnaka", "matnaka", "metnakeh", "mtnake",
    "metnake", "metnka",
    # معرص
    "mعrs", "moعrs", "mعars", "moعars",
    # ديوث
    "dayouth", "dayoth", "dayot", "dayos", "diawth", "dayuth", "dayuuth",
    # "يا+شتيمة" بدون مسافة (لا تحتوي لبس مع الكلمات النظيفة)
    "yaحmar", "yaحmara", "yaحomar", "yaحimar", "yakosom", "yakosomak",
    "yakosmak", "yaksomak", "yaخara", "yakhara", "yaخra", "yakhra",
    "yaخwal", "yakhawal", "yakhwal", "yakhol", "yasharmota", "yasharmoota",
    "yasharmuta", "yaqahba", "yagahba", "yakahba", "yamanyak", "yamenyak",
    "yametnak", "yamatnak", "yakhanzir", "yakhinzir", "yadayouth", "yadayoth",
    "yakalb", "yaklb", "yazeb", "yazob", "yazub", "yatez", "yatiz",
    "yaعrs", "yaars", "yaعars", "yaneek", "yanik", "yanek",
]

_FRANCO_EXACT = {
    "kos", "kus", "kis", "ks", "kosk", "kusk", "koss", "kuss",
    "zeb", "zebr", "zob", "zub", "zbr", "zubr", "zibi", "zebi",
    "tez", "tiz", "tyz", "طez", "طiz", "teez", "tezz", "tizy", "tezi",
    "nek", "nik", "neyk", "nayk", "naik", "nyk", "nak", "neek", "neik",
    "ars", "عrs", "عars",
    "kalb", "klb", "kelb",
    # حمار (must stay exact: "a7mar" = red = أحمر is innocent)
    "حmar", "حomar", "حimar", "homar", "hmar", "himar",
    "حmara", "hmara", "homara", "himara", "حmare",
    # خرا (must stay exact: "a5ra" = أخرى is innocent)
    "khara", "khra", "خara", "خra", "khare", "خare", "kharaa",
    # بضان (must stay exact: "abdan" = أبداً is innocent)
    "bdan",
    # أحا
    "aحa", "eحa", "aحه", "ahha",
}


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def _compactify(phrase: str, strip: re.Pattern) -> str:
    return strip.sub("", phrase)


_AR_PHRASES_C = [_compactify(p, _ARABIC_COMPACT) for p in _AR_PHRASES]
_EN_PHRASES_C = [_compactify(p, _LATIN_COMPACT) for p in _EN_PHRASES]
_FRANCO_PHRASES_C = [_compactify(p, _FRANCO_COMPACT) for p in _FRANCO_PHRASES]


def _match_lists(phrases_c, strong, exact, spaced, compact) -> bool:
    for c in phrases_c:
        if c and c in compact:
            return True
    for word in strong:
        if word in compact:
            return True
    tokens = set(spaced.split())
    for word in exact:
        if word in tokens:
            return True
    # Whole-text compact equality catches the letter-spacing dodge for short
    # slurs ("ك س" -> "كس", "a s s" -> "ass") without substring false positives.
    return bool(compact) and compact in exact


def _match_arabic(spaced: str, compact: str) -> bool:
    return _match_lists(_AR_PHRASES_C, _AR_STRONG, _AR_EXACT, spaced, compact)


def _match_english(spaced: str, compact: str) -> bool:
    return _match_lists(_EN_PHRASES_C, _EN_STRONG, _EN_EXACT, spaced, compact)


def _match_franco(spaced: str, compact: str) -> bool:
    return _match_lists(_FRANCO_PHRASES_C, _FRANCO_STRONG, _FRANCO_EXACT, spaced, compact)


def contains_profanity(text: str) -> bool:
    """Return True when ``text`` contains profanity, insults or slurs.

    Runs the Arabic-script matcher, then — when any Latin letter is present —
    the English (with leet) and Franco-Arabic (with digit) matchers.
    """
    t = _prepare(text)
    if not t.strip():
        return False

    ar_spaced = _arabic_spaced(t)
    ar_compact = _arabic_compact(t)
    if ar_compact and _match_arabic(ar_spaced, ar_compact):
        return True

    if re.search(r"[a-zA-Z]", t):
        en_spaced = _english_spaced(t)
        en_compact = _english_compact(t)
        fr_spaced = _franco_spaced(t)
        fr_compact = _franco_compact(t)
        if _match_english(en_spaced, en_compact):
            return True
        if _match_franco(fr_spaced, fr_compact):
            return True

    return False
