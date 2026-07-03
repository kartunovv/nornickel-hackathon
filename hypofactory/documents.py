"""Загрузка литературы: PDF (PyMuPDF) и DOCX → чанки с метаданными.

Каждый чанк несёт источник (файл, страница) — это основа цитирования
в карточках доказательств гипотез.
"""
from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, asdict
from pathlib import Path
from xml.etree import ElementTree as ET

_DOCX_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}

# Ключевые слова предметной области: чанк без единого совпадения не индексируем
# (книги содержат сотни страниц нерелевантных разделов — золото, платина и т.п.)
DOMAIN_KEYWORDS = [
    "флотац", "измельчен", "классификац", "гидроциклон", "мельниц", "грохо",
    "ксантогенат", "собират", "вспенива", "депрессор", "активатор", "реагент",
    "пентландит", "пирротин", "халькопирит", "миллерит", "сульфид", "никел",
    "медь", "медн", "сростк", "раскрыт", "крупност", "шлам", "пульп",
    "концентрат", "хвост", "обогащен", "извлечен", "перечист", "аэрац",
    "магнитн", "гравитац", "сепарац", "футеровк", "шаров", "стержнев",
    "плотность пульпы", "аэрофлот", "дитиофосфат", "известь", "сода",
    "флотореагент", "камер", "пенн", "минерал",
]


@dataclass
class Chunk:
    chunk_id: str
    text: str
    source: str        # имя файла
    page: int | None   # страница (для PDF)
    kind: str          # book | article | report | scheme

    def to_dict(self) -> dict:
        return asdict(self)

    def cite(self) -> str:
        p = f", с. {self.page}" if self.page else ""
        return f"{self.source}{p}"


def _clean(text: str) -> str:
    text = text.replace("\xad", "").replace("¬", "")
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)   # переносы слов
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _is_relevant(text: str) -> bool:
    low = text.lower()
    hits = sum(1 for kw in DOMAIN_KEYWORDS if kw in low)
    return hits >= 2


def _split_long(text: str, max_chars: int = 1800) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    parts, buf = [], ""
    for para in re.split(r"(?<=[.!?])\s+", text):
        if len(buf) + len(para) > max_chars and buf:
            parts.append(buf.strip())
            buf = ""
        buf += para + " "
    if buf.strip():
        parts.append(buf.strip())
    return parts


def load_pdf(path: str | Path, kind: str = "book",
             filter_domain: bool = True) -> list[Chunk]:
    import pymupdf

    path = Path(path)
    doc = pymupdf.open(path)
    chunks: list[Chunk] = []
    for page_no in range(len(doc)):
        text = _clean(doc[page_no].get_text())
        if len(text) < 200:
            continue
        if filter_domain and not _is_relevant(text):
            continue
        for j, piece in enumerate(_split_long(text)):
            if len(piece) < 150:
                continue
            chunks.append(Chunk(
                chunk_id=f"{path.stem[:40]}_p{page_no + 1}_{j}",
                text=piece, source=path.name, page=page_no + 1, kind=kind))
    doc.close()
    return chunks


def load_docx(path: str | Path, kind: str = "report") -> list[Chunk]:
    path = Path(path)
    z = zipfile.ZipFile(path)
    root = ET.fromstring(z.read("word/document.xml"))
    body = root.find("w:body", _DOCX_NS)
    lines: list[str] = []

    def walk(el):
        for child in el:
            tag = child.tag.split("}")[1]
            if tag == "p":
                texts = [t.text or "" for t in child.iter(
                    "{%s}t" % _DOCX_NS["w"])]
                line = "".join(texts).strip()
                if line:
                    lines.append(line)
            elif tag == "tbl":
                for row in child.findall("w:tr", _DOCX_NS):
                    cells = []
                    for tc in row.findall("w:tc", _DOCX_NS):
                        texts = [t.text or "" for t in tc.iter(
                            "{%s}t" % _DOCX_NS["w"])]
                        cells.append("".join(texts).strip())
                    lines.append(" | ".join(cells))
            else:
                walk(child)

    walk(body)
    full = _clean("\n".join(lines))
    return [Chunk(chunk_id=f"{path.stem[:40]}_{j}", text=piece,
                  source=path.name, page=None, kind=kind)
            for j, piece in enumerate(_split_long(full))]


def load_directory(directory: str | Path, filter_domain: bool = True) -> list[Chunk]:
    """Загружает все PDF/DOCX из каталога (рекурсивно)."""
    directory = Path(directory)
    chunks: list[Chunk] = []
    for path in sorted(directory.rglob("*")):
        if path.suffix.lower() == ".pdf":
            chunks.extend(load_pdf(path, filter_domain=filter_domain))
        elif path.suffix.lower() == ".docx" and not path.name.startswith("~$"):
            chunks.extend(load_docx(path))
    return chunks
