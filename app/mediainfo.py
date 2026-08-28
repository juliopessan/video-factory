"""Leitura da duração de um arquivo de vídeo sem depender de ffmpeg.

Suporta os dois contêineres que interessam aqui: ISO-BMFF (MP4/MOV, via o átomo
`mvhd`) e Matroska/WebM (via o elemento `Duration` do `Info`). Qualquer outro
formato — ou um arquivo truncado — devolve `None`, e quem chama trata isso como
"duração desconhecida" em vez de rejeitar o upload.
"""
from __future__ import annotations

import struct
from pathlib import Path

MAX_SCAN_BYTES = 8 * 1024 * 1024  # basta o cabecalho: nao lemos o arquivo inteiro


def probe_duration_seconds(path: str | Path) -> float | None:
    """Duração em segundos, ou None quando o formato não é reconhecido."""
    data = Path(path).open("rb").read(MAX_SCAN_BYTES)
    if not data:
        return None
    if data[:4] == b"\x1a\x45\xdf\xa3":
        return _matroska_duration(data)
    return _isobmff_duration(data)


# --------------------------------------------------------------------- MP4/MOV


def _isobmff_duration(data: bytes) -> float | None:
    moov = _find_box(data, b"moov")
    if moov is None:
        return None
    mvhd = _find_box(data, b"mvhd", *moov)
    if mvhd is None:
        return None
    start, end = mvhd
    body = data[start:end]
    if len(body) < 20:
        return None
    version = body[0]
    try:
        if version == 1:
            timescale, duration = struct.unpack(">IQ", body[20:32])
        else:
            timescale, duration = struct.unpack(">II", body[12:20])
    except struct.error:
        return None
    if not timescale or duration in (0, 0xFFFFFFFF, 0xFFFFFFFFFFFFFFFF):
        return None
    return duration / timescale


def _find_box(data: bytes, kind: bytes, start: int = 0, end: int | None = None):
    """Percorre os átomos de um nível e devolve (inicio, fim) do conteúdo."""
    end = len(data) if end is None else end
    cursor = start
    while cursor + 8 <= end:
        (size,) = struct.unpack(">I", data[cursor : cursor + 4])
        box_type = data[cursor + 4 : cursor + 8]
        header = 8
        if size == 1:  # tamanho estendido de 64 bits
            if cursor + 16 > end:
                return None
            (size,) = struct.unpack(">Q", data[cursor + 8 : cursor + 16])
            header = 16
        elif size == 0:  # vai ate o fim do arquivo
            size = end - cursor
        if size < header:
            return None
        if box_type == kind:
            return cursor + header, min(cursor + size, end)
        cursor += size
    return None


# ------------------------------------------------------------------ WebM/MKV


def _matroska_duration(data: bytes) -> float | None:
    """Procura o par TimecodeScale/Duration dentro do elemento Info."""
    info = data.find(b"\x15\x49\xa9\x66")  # ID do elemento Info
    if info < 0:
        return None
    window = data[info : info + 4096]
    timecode_scale = 1_000_000.0
    scale_at = window.find(b"\x2a\xd7\xb1")
    if scale_at >= 0:
        value = _ebml_uint(window, scale_at + 3)
        if value:
            timecode_scale = float(value)
    duration_at = window.find(b"\x44\x89")
    if duration_at < 0:
        return None
    size, offset = _ebml_size(window, duration_at + 2)
    if size not in (4, 8) or offset + size > len(window):
        return None
    raw = window[offset : offset + size]
    ticks = struct.unpack(">f" if size == 4 else ">d", raw)[0]
    return ticks * timecode_scale / 1_000_000_000.0


def _ebml_size(buffer: bytes, index: int) -> tuple[int, int]:
    """Decodifica um tamanho EBML: devolve (tamanho, posicao do conteudo)."""
    if index >= len(buffer):
        return 0, index
    first = buffer[index]
    length = 1
    while length <= 8 and not (first & (0x80 >> (length - 1))):
        length += 1
    if length > 8:
        return 0, index
    value = first & (0xFF >> length)
    for offset in range(1, length):
        if index + offset >= len(buffer):
            return 0, index
        value = (value << 8) | buffer[index + offset]
    return value, index + length


def _ebml_uint(buffer: bytes, index: int) -> int | None:
    size, offset = _ebml_size(buffer, index)
    if not size or offset + size > len(buffer):
        return None
    return int.from_bytes(buffer[offset : offset + size], "big")
