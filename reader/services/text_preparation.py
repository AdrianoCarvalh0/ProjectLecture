import re


ACADEMIC_REPLACEMENTS = (
    (re.compile(r"\bvol\.\s*(?=\d)", re.IGNORECASE), "volume "),
    (re.compile(r"\bn[º°]\s*(?=\d)", re.IGNORECASE), "número "),
    (re.compile(r"\bp\.\s*(?=\d)", re.IGNORECASE), "página "),
    (re.compile(r"\bpp\.\s*(?=\d)", re.IGNORECASE), "páginas "),
    (re.compile(r"\bet al\.", re.IGNORECASE), "e colaboradores"),
    (re.compile(r"\bfig\.\s*(?=\d)", re.IGNORECASE), "figura "),
    (re.compile(r"\be\.g\.\b", re.IGNORECASE), "por exemplo"),
    (re.compile(r"\bi\.e\.\b", re.IGNORECASE), "isto é"),
    (re.compile(r"(?<=\d)\s*%(?=\s|[.,;:]|$)"), " por cento"),
)


def prepare_for_speech(text, mode="academic"):
    """Prepara pontuação e abreviações sem alterar o texto mostrado ao leitor."""
    spoken = re.sub(r"\s+", " ", text).strip()
    spoken = re.sub(r"https?://\S+", " endereço eletrônico ", spoken)
    spoken = re.sub(r"\bdoi:\s*\S+", " identificador D O I ", spoken, flags=re.IGNORECASE)
    if mode == "academic":
        for pattern, replacement in ACADEMIC_REPLACEMENTS:
            spoken = pattern.sub(replacement, spoken)
        spoken = re.sub(r"\[(\d+(?:\s*[-,;]\s*\d+)*)\]", r" referência \1 ", spoken)
    elif mode == "natural":
        spoken = re.sub(r"\[(\d+(?:\s*[-,;]\s*\d+)*)\]", "", spoken)
    return re.sub(r"\s+", " ", spoken).strip()
