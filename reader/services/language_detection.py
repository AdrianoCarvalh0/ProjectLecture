import re
import unicodedata
from collections import Counter


TOKEN_PATTERN = re.compile(r"[a-z]+")
ENGLISH_MARKERS = {
    "the",
    "and",
    "of",
    "to",
    "in",
    "for",
    "with",
    "that",
    "this",
    "from",
    "were",
    "was",
    "are",
    "is",
    "as",
    "by",
    "on",
    "which",
    "study",
    "results",
    "method",
    "conclusion",
    "abstract",
    "between",
    "we",
    "our",
}
PORTUGUESE_MARKERS = {
    "de",
    "da",
    "do",
    "das",
    "dos",
    "e",
    "que",
    "o",
    "a",
    "em",
    "para",
    "com",
    "uma",
    "um",
    "por",
    "como",
    "foi",
    "sao",
    "este",
    "esta",
    "entre",
    "estudo",
    "resultados",
    "metodo",
    "conclusao",
    "resumo",
    "nos",
    "nas",
}


def _normalized_tokens(text):
    normalized = unicodedata.normalize("NFKD", (text or "")[:40_000])
    normalized = "".join(
        character
        for character in normalized
        if not unicodedata.combining(character)
    ).lower()
    return TOKEN_PATTERN.findall(normalized)


def looks_like_english(text):
    """Detecta artigos em inglês sem enviar conteúdo a serviços externos."""
    counts = Counter(_normalized_tokens(text))
    english_score = sum(counts[word] for word in ENGLISH_MARKERS)
    portuguese_score = sum(counts[word] for word in PORTUGUESE_MARKERS)
    return english_score >= 5 and english_score > portuguese_score * 1.25
