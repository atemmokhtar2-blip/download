"""Strong profanity / insult filter for outbound "message to admin" content.

Matching is layered so it is both broad and precise:

  * Arabic script   — MSA + Egyptian/Levantine/Gulf slurs, normalized (hamza /
                      teh marbuta / alef-maqsura variants, diacritics, tatweel).
  * Franco / Arabizi — Latin-script Arabic ("kosomak", "7mar", "5ara", "9a7ba");
                       the 3/5/6/7/8/9 digits map to ع/خ/ط/ح/غ/ق so every digit
                       spelling resolves to one canonical form.
  * English         — profanity plus leet substitutions (@/$/1/0/3/7/9/8/4/!).
                      A "compact" form removes separators so spaced/period
                      dodging like "f.u.c.k" still resolves to "fuck".

Precision rules (per list):

  * PHRASES  — multi-word curses, matched as substrings anywhere.
  * STRONG   — longer unambiguous slurs, matched as substrings anywhere.
  * EXACT    — short/ambiguous terms, matched only as whole tokens so they
               cannot false-positive inside unrelated words ("ass" in "class").
"""

from __future__ import annotations

import re
import unicodedata

# ---------------------------------------------------------------------------
# Normalization primitives
# ---------------------------------------------------------------------------

# Arabic diacritics + tatweel (ـ).
_DIACRITICS = re.compile(r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED\u0640]")

# Keep letters/digits; everything else (emoji, punctuation, symbols) becomes a
# single space so "كس-أمك" and "كس 😡 أمك" both normalize to "كس امك".
_NON_WORD = re.compile(r"[^\w\u0621-\u064A]", re.UNICODE)

# Collapse 3+ identical letters ("شششمووط" -> "شموط", "fuuuck" -> "fuck").
_REPEAT3 = re.compile(r"(.)\1{2,}")

# Collapse 2+ identical letters — in Arabizi doubling is just emphasis/shadda
# ("sharmoota" -> "sharmota", "teez" -> "tez", "kharra" -> "khara").
_REPEAT2 = re.compile(r"(.)\1+")

# Compact form: keep a-z / digits / Arabic letters, strip everything else so
# "f.u.c.k" -> "fuck" and "كس-أمك" -> "كسأمك".
_COMPACT_STRIP = re.compile(r"[^a-z0-9\u0621-\u064A]")

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


def normalize_profanity_text(text: str) -> str:
    """Return the canonical Arabic-script comparison form (public API)."""
    t = unicodedata.normalize("NFKC", str(text or ""))
    t = _DIACRITICS.sub("", t)
    t = t.translate(_ARABIC_TRANSLIT)
    t = t.casefold()
    t = _NON_WORD.sub(" ", t)
    t = _REPEAT3.sub(r"\1", t)
    return re.sub(r"\s+", " ", t).strip()


def _normalize_latin(text: str) -> tuple[str, str, str]:
    """Return (english_spaced, english_compact, franco) normalized forms."""
    t = unicodedata.normalize("NFKC", str(text or "")).casefold()

    english = t.translate(_LEET)
    english = _NON_WORD.sub(" ", english)
    english = _REPEAT3.sub(r"\1", english)
    english = re.sub(r"\s+", " ", english).strip()

    compact = t.translate(_LEET)
    compact = _COMPACT_STRIP.sub("", compact)
    compact = _REPEAT3.sub(r"\1", compact)

    franco = t.translate(_FRANCO_DIGITS)
    franco = _NON_WORD.sub(" ", franco)
    franco = _REPEAT2.sub(r"\1", franco)
    franco = re.sub(r"\s+", " ", franco).strip()

    return english, compact, franco


# ---------------------------------------------------------------------------
# Arabic lists (canonical ا / ي / ه, no diacritics)
# ---------------------------------------------------------------------------

_AR_PHRASES = [
    # الأم / الأخت
    "كس امك", "كس ام", "كس اختك", "كس اخت", "كس ابوك", "كس خواتك",
    "كسمك", "كسم امك", "كسم اختك", "كسم ابوك", "كسم عيلتك", "كس عيلتك",
    "امك شرموطه", "اختك شرموطه", "امك قحبه", "اختك قحبه", "امك عاهره",
    "اختك عاهره", "امك متناكه", "امك زانيه",
    # اللعن
    "يلعن دينك", "يلعن ابوك", "يلعن امك", "يلعن ربك", "يلعن جدك",
    "يلعن عرضك", "يلعن خواتك", "الله يلعنك", "ربنا يلعنك", "الله يلعن",
    "يخرب بيتك", "الله يخرب بيتك", "عليك اللعنه", "اللعنه عليك",
    "كسم الدين", "يلعن دين",
    # ابن / بنت + نعت
    "ابن الكلب", "بنت الكلب", "ابن القحبه", "ابن الشرموطه", "ابن العاهر",
    "ابن العاهره", "ابن المتناكه", "ابن المنتاكه", "ابن الوسخه", "ابن الزانيه",
    "ابن العرص", "ابن الديوث", "يا ابن الكلب", "يا ابن القحبه", "يا ابن الشرموطه",
    # يا + سب
    "يا كلب", "يا حمار", "يا حيوان", "يا قرد", "يا خنزير", "يا بغل",
    "يا جاموسه", "يا تيس", "يا شرموط", "يا قحبه", "يا عاهر", "يا منيوك",
    "يا متناك", "يا خول", "يا عرص", "يا معرص", "يا ديوث", "يا زباله",
    "يا وسخ", "يا نجس", "يا قذر", "يا حقير", "يا غبي", "يا اهبل", "يا زانيه",
    # سبّ صريح / تهديد
    "على زبي", "على طيزي", "في طيزي", "على كسمك", "اكل زبي", "اكل طيزي",
    "روح انيك", "انيكك", "روح انيك نفسك", "امك في طيزي", "اختك في طيزي",
]

_AR_STRONG = [
    # جنسية
    "شرموط", "شرموطه", "شراميط", "شراميت",
    "قحبه", "قحاب",
    "عاهر", "عاهره", "عواهر",
    "منيوك", "منيوكه", "منيوكين", "منيوكات",
    "متناك", "متناكه", "متناكين",
    "منتاك", "منتاكه",
    "منيك", "منيكه",
    "نيج", "نياك", "نياكه", "ينيك", "تنيك", "نايك", "ناكت", "نيكها",
    "زاني", "زانيه",
    "ديوث", "دياثه", "قواد",
    # أعضاء
    "طيزه", "طيزك", "طياز", "طيوز",
    # إهانات
    "معرص", "معرصه",
    "بضان", "بظين",
    "حمار", "حمير", "خنزير", "خنازير",
    "احمق", "متخلف", "متخلفه", "ساقط", "ساقطه", "معتوه", "معتوهه",
    "عبيط", "عبيطه", "مقرف", "مقرفه",
]

_AR_EXACT = {
    "زب", "زبي", "زبك", "زبر", "اير", "ايري", "عير", "طيز", "كس", "كسم",
    "نيك", "نيج", "نياك", "احا", "عرص", "خول", "خوال", "خولات",
    "كلب", "كلاب", "غبي", "اهبل", "وسخ", "نجس", "قذر", "حقير", "زباله",
    "تافه", "جبان", "تفو", "زفت", "خرا", "خره", "ابله", "هبله", "بليد",
}

# ---------------------------------------------------------------------------
# English lists
# ---------------------------------------------------------------------------

_EN_PHRASES = [
    "fuck you", "fuck u", "fuck ur", "fuck your", "fuck off", "fuck urself",
    "fuck yourself", "fucking you", "mother fucker", "mutha fucka",
    "son of a bitch", "piece of shit", "piece of crap", "kiss my ass",
    "kiss my arse", "suck my", "suck a", "suck my dick", "suck my cock",
    "suck it", "you suck", "u suck", "blow me", "eat shit", "eat a dick",
    "go to hell", "go fuck", "shut the fuck", "what the fuck",
    "shut up bitch", "son of bitch", "stfu", "gtfo",
]

_EN_STRONG = [
    "fuck", "fck", "fcuk", "fock",
    "fucking", "fuckin", "fucker", "fuckers", "fucked",
    "motherfucker", "motherfucking", "muthafucka", "mothafucka", "muthafuckin",
    "shit", "bullshit", "shithead", "shitty",
    "bitch", "bitches", "bitching", "bitchy",
    "asshole", "assholes", "bastard", "bastards",
    "dickhead", "dickheads", "dumbass", "dumbasses", "jackass", "jackasses",
    "faggot", "faggots", "nigger", "nigga",
    "cocksucker", "cocksuckers", "douche", "douchebag",
    "retarded", "whore", "whores", "slut", "sluts", "slutty",
    "porn", "porno",
]

_EN_EXACT = {
    "ass", "arse", "dick", "dicks", "cock", "cocks", "cunt", "cunts",
    "cum", "cums", "jizz", "jism", "pussy", "pussies",
    "hoe", "hoes", "thot", "thots", "fag", "fags",
    "prick", "twat", "twats", "wanker", "wankers", "bollocks", "bollock",
    "bellend", "nonce", "fuk", "fuq", "sht", "shite",
}

# ---------------------------------------------------------------------------
# Franco-Arabic lists (digits already mapped to ع/خ/ط/ح/غ/ق)
# ---------------------------------------------------------------------------

_FRANCO_PHRASES = [
    "kos omak", "kos omk", "kos emk", "kos emak", "kus omak", "kus omk",
    "kos omm", "kos om", "kos mk", "kus mk", "kis omak", "kis omk",
]

_FRANCO_STRONG = [
    "kosom", "kosum", "kusom", "kusum", "ksom", "kosm", "ksmk", "kosmk",
    "sharmota", "sharmuta", "shermota", "shermuta", "sharmot", "sharmut",
    "sharmu", "sharmoط", "sharmoطa", "charmota", "charmuta", "sharmout",
    "qahb", "gahb", "kahb", "قahb", "قaح", "qaح", "gaح", "kaح",
    "خwal", "خawal", "خol", "خwl", "khawal", "khwal", "khwl", "khol", "khuwal",
    "menyak", "manyak", "mnayak", "mnayek", "menyok", "mnayk", "mnyk",
    "metnak", "matnak", "mtnak",
    "خara", "خra", "خare",
    "khanzir", "khinzir", "khanzer", "khinzer", "خanzir", "خnzir",
    "حmar", "حomar", "حimar",
    "mعrs", "moعrs", "mعars", "moعars",
    "dayouth", "dayoth", "dayot", "dayos", "diawth",
]

_FRANCO_EXACT = {
    "kos", "kus", "kis", "ks",
    "zeb", "zebr", "zob", "zub", "zbr", "zubr", "zibi",
    "tez", "tiz", "tyz", "طez", "طiz",
    "nek", "nik", "neyk", "nayk", "naik", "nyk", "nak",
    "ars", "عrs", "عars",
    "kalb", "klb",
    "khara", "khra", "homar", "hmar", "himar",
    "bdan", "aحa",
}


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def _match_arabic(norm: str) -> bool:
    for phrase in _AR_PHRASES:
        if phrase in norm:
            return True
    for word in _AR_STRONG:
        if word in norm:
            return True
    tokens = set(norm.split())
    return any(w in tokens for w in _AR_EXACT)


def _match_english(spaced: str, compact: str) -> bool:
    for phrase in _EN_PHRASES:
        if phrase in spaced:
            return True
    for word in _EN_STRONG:
        if word in compact or word in spaced:
            return True
    tokens = set(spaced.split())
    return any(w in tokens for w in _EN_EXACT)


def _match_franco(franco: str) -> bool:
    for phrase in _FRANCO_PHRASES:
        if phrase in franco:
            return True
    for word in _FRANCO_STRONG:
        if word in franco:
            return True
    tokens = set(franco.split())
    return any(w in tokens for w in _FRANCO_EXACT)


def contains_profanity(text: str) -> bool:
    """Return True when ``text`` contains profanity, insults or slurs.

    Runs the Arabic-script matcher, then — when any Latin letter is present —
    the English (with leet) and Franco-Arabic (with digit) matchers.
    """
    s = str(text or "")
    if not s.strip():
        return False

    if _match_arabic(normalize_profanity_text(s)):
        return True

    if re.search(r"[a-zA-Z]", s):
        english, compact, franco = _normalize_latin(s)
        if _match_english(english, compact):
            return True
        if _match_franco(franco):
            return True

    return False
