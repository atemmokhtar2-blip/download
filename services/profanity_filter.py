"""Strong profanity / insult filter for outbound "message to admin" content.

All matching runs on a normalized form of the text (Arabic alef/hamza/teh
marbuta/alef-maqsura variants collapsed, diacritics stripped, case folded,
non-letter runs turned into single spaces) so common bypasses — different
hamza forms, taa marbuta vs haa, tatweel, extra spaces, emoji separators —
still match. Repeated letters are also squeezed so exaggeration like
"شششمووط" still collapses toward the real word.

The word list is deliberately split by match precision:

  * PHRASES        — multi-word curses, matched as substrings anywhere.
  * STRONG_SUBSTR  — longer unambiguous slurs, matched as substrings anywhere
                      (e.g. "الشرموط" still contains "شرموط").
  * EXACT_WORDS    — short/ambiguous terms, matched only as whole tokens so
                      they cannot false-positive inside unrelated words.

Extend any of the three lists freely; normalization happens automatically.
"""

from __future__ import annotations

import re
import unicodedata

# Arabic diacritics + tatweel (ـ). Stripping these makes "شَرْمُوط" == "شرموط".
_DIACRITICS = re.compile(r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED\u0640]")

# Keep letters/digits; everything else (emoji, punctuation, symbols) becomes a
# single space so "كس-أمك" and "كس 😡 أمك" both normalize to "كس امك".
_NON_WORD = re.compile(r"[^\w\u0621-\u064A]", re.UNICODE)

# Collapse 3+ identical letters (e.g. "ككككسم" -> "كسم", "شششمووط" -> "شموط").
_REPEAT = re.compile(r"(.)\1{2,}")


def normalize_profanity_text(text: str) -> str:
    """Return a canonical comparison form used for profanity matching."""
    t = unicodedata.normalize("NFKC", str(text or ""))
    t = _DIACRITICS.sub("", t)
    # Arabic letter-shape variants -> one canonical form.
    t = t.replace("\u0649", "\u064A")  # ى  -> ي (alef maqsura)
    t = t.replace("\u0629", "\u0647")  # ة  -> ه (taa marbuta)
    t = t.replace("\u0623", "\u0627")  # أ  -> ا
    t = t.replace("\u0625", "\u0627")  # إ  -> ا
    t = t.replace("\u0622", "\u0627")  # آ  -> ا
    t = t.replace("\u0624", "\u0648")  # ؤ  -> و
    t = t.replace("\u0626", "\u064A")  # ئ  -> ي
    t = t.casefold()
    t = _NON_WORD.sub(" ", t)
    t = _REPEAT.sub(r"\1", t)
    return re.sub(r"\s+", " ", t).strip()


# NOTE: every Arabic entry below is already in canonical form (use ا / ي / ه,
# no diacritics) because matching runs on the normalized text.

_PHRASES = [
    # جنسية صريحة موجهة
    "كس امك", "كس ام", "كس اختك", "كس اخت", "كسمك", "كسم امك", "كسم اختك",
    "امك شرموطه", "اختك شرموطه", "امك قحبه", "اختك قحبه",
    # سبّ ولعن صريح
    "يلعن دينك", "يلعن ابوك", "يلعن امك", "يلعن ربك", "يلعن جدك",
    "الله يلعنك", "ربنا يلعنك",
    # سبّ بالأهل / الحيوان
    "ابن الكلب", "بنت الكلب", "ابن القحبه", "ابن الشرموطه", "ابن العاهر",
    "يا كلب", "يا حمار", "يا حيوان", "يا قرد", "يا خنزير", "يا بغل",
    # إنجليزية صريحة
    "son of a bitch", "piece of shit", "suck my dick", "suck my",
]

_STRONG_SUBSTR = [
    # عربي
    "شرموط", "شرموطه", "شراميط", "شراميت",
    "قحبه", "قحاب",
    "عاهر", "عاهره", "عواهر",
    "منيوك", "منيوكه", "منيوكين",
    "متناك", "متناكه", "متناكين",
    "منيك", "منيكه",
    "نيج", "نياك", "نياكه", "ينيك",
    "طيزه", "طيزك",
    "معرص", "معرصه",
    "بضان", "بظين",
    "حمار", "خنزير",
    "احمق", "متخلف", "متخلفه", "ساقط", "ساقطه", "معتوه", "معتوهه",
    # إنجليزي
    "fucking", "fucker", "motherfucker", "muthafucka", "motherfucking",
    "bitch", "asshole", "bastard", "whore", "slut", "pussy", "cunt",
    "dickhead", "dumbass", "jackass", "shithead", "faggot", "nigger", "nigga",
]

_EXACT_WORDS = {
    "زب", "زبي", "زبك", "زبر", "اير", "ايري", "طيز", "كس", "كسم",
    "نيك", "نيج", "احا", "عرص", "خول",
    "كلب", "غبي", "اهبل", "وسخ", "نجس", "قذر", "حقير", "زباله",
    "تافه", "جبان", "تفو",
    "fuck", "shit", "dick", "cum", "cock", "ass", "hoe", "porn",
}


def contains_profanity(text: str) -> bool:
    """Return True when ``text`` contains profanity, insults or slurs.

    Strong-but-careful matching: unambiguous phrases and longer slurs match
    as substrings, while short/ambiguous terms must be whole tokens so they
    cannot fire inside unrelated words (e.g. "ass" inside "class").
    """
    norm = normalize_profanity_text(text)
    if not norm:
        return False
    for phrase in _PHRASES:
        if phrase in norm:
            return True
    for word in _STRONG_SUBSTR:
        if word in norm:
            return True
    tokens = set(norm.split())
    for word in _EXACT_WORDS:
        if word in tokens:
            return True
    return False
