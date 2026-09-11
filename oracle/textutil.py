"""Text normalisation shared by split / load / cognee."""
from __future__ import annotations

import re
import unicodedata


def clean(text: str) -> str:
    """text_clean for BM25: TeX commands and symbols dropped, lowercase, tokens <= 40 bytes."""
    t = unicodedata.normalize("NFKC", text or "")
    t = re.sub(r"\\[a-zA-Z]+", " ", t)
    t = re.sub(r"[^0-9A-Za-z]+", " ", t).lower()
    return " ".join(w for w in t.split() if len(w.encode()) <= 40)
