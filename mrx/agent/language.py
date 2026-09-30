"""Lightweight language detection (script + romanised-Hindi markers). It is a hint for the model and the
voice layer, not a classifier of record; the LLM still mirrors whatever language the user actually writes."""
from __future__ import annotations

import re

SCRIPTS = [  # (regex, code, name)
    (r"[਀-੿]", "pa", "Punjabi"), (r"[ঀ-৿]", "bn", "Bengali"), (r"[઀-૿]", "gu", "Gujarati"),
    (r"[஀-௿]", "ta", "Tamil"), (r"[ఀ-౿]", "te", "Telugu"), (r"[ಀ-೿]", "kn", "Kannada"),
    (r"[ഀ-ൿ]", "ml", "Malayalam"), (r"[଀-୿]", "or", "Odia"),
    (r"[؀-ۿ]", "ur", "Urdu/Arabic-script"), (r"[一-鿿]", "zh", "Chinese"),
    (r"[぀-ヿ]", "ja", "Japanese"), (r"[가-힯]", "ko", "Korean"), (r"[Ѐ-ӿ]", "ru", "Cyrillic"),
    (r"[ऀ-ॿ]", "hi", "Hindi"),
]
MARATHI = {"आहे", "नाही", "मी", "काय", "तुम्ही", "करा", "उघडा"}
HINGLISH = {"kya", "hai", "hain", "karo", "kar", "kholo", "khol", "chalao", "chala", "mujhe", "mera", "meri", "mere", "batao",
            "dikhao", "bhejo", "nahi", "nahin", "aur", "band", "bana", "banao", "dhundo", "ruko", "rukk", "roko", "abhi",
            "wala", "wali", "isko", "usko", "kaise", "kaun", "kab", "kahan", "ko", "se", "ka", "ki", "ke", "haan", "acha"}
FORMULA = {"hey", "hi", "hello"}


def detect_language(text: str) -> dict:
    for rx, code, name in SCRIPTS:
        if re.search(rx, text):
            if code == "hi" and any(w in text for w in MARATHI):
                return {"code": "mr", "name": "Marathi", "script": "Devanagari", "confidence": "heuristic"}
            return {"code": code, "name": name, "script": "non-Latin", "confidence": "heuristic"}
    words = set(re.findall(r"[a-z]+", text.lower()))
    hits = len(words & HINGLISH)
    if hits >= 2 or (hits >= 1 and len(words) <= 4 and words & HINGLISH - {"ka", "ki", "ke", "ko", "se", "aur"}):
        return {"code": "hi-Latn", "name": "Hinglish", "script": "Latin", "confidence": "heuristic"}
    return {"code": "en", "name": "English", "script": "Latin", "confidence": "heuristic"}
