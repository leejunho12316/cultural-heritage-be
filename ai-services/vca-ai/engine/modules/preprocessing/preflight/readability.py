"""Deterministic structural readability checks for supported image formats."""

import zlib

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PNG_IHDR_LENGTH = 13
PNG_CHUNK_OVERHEAD = 12
PNG_MINIMUM_CHUNKS = 3
GIF_MINIMUM_LENGTH = 14
JPEG_MARKER_PREFIX = 0xFF
JPEG_END_MARKER = 0xD9
JPEG_FRAME_MINIMUM_LENGTH = 11
JPEG_COMPONENT_SIZE = 3
JPEG_FRAME_MARKERS = (
    *range(0xC0, 0xC4),
    *range(0xC5, 0xC8),
    *range(0xC9, 0xCC),
    *range(0xCD, 0xD0),
)


def _uint(data: bytes) -> int:
    return int.from_bytes(data, byteorder="big")


# PNG 시그니처 이후를 청크 단위로 순회하며 (타입, 데이터)로 파싱하고, CRC가
# 맞지 않거나 길이가 데이터 범위를 벗어나면 None을 반환한다.
# _png_is_readable에서 호출된다.
def _png_chunks(data: bytes) -> tuple[tuple[bytes, bytes], ...] | None:
    position = len(PNG_SIGNATURE)
    chunks: list[tuple[bytes, bytes]] = []
    while position < len(data):
        if position + PNG_CHUNK_OVERHEAD > len(data):
            return None
        length = _uint(data[position : position + 4])
        chunk_end = position + PNG_CHUNK_OVERHEAD + length
        if chunk_end > len(data):
            return None
        chunk_type = data[position + 4 : position + 8]
        chunk_data = data[position + 8 : chunk_end - 4]
        chunk_crc = _uint(data[chunk_end - 4 : chunk_end])
        if zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF != chunk_crc:
            return None
        chunks.append((chunk_type, chunk_data))
        position = chunk_end
    return tuple(chunks)


# PNG 바이트가 시그니처, IHDR/IEND 청크 구조, 압축 해제 가능한 IDAT까지
# 구조적으로 온전한지 검사한다. readable_mime_type에서 호출된다.
def _png_is_readable(data: bytes) -> bool:
    if not data.startswith(PNG_SIGNATURE):
        return False
    chunks = _png_chunks(data)
    if chunks is None or len(chunks) < PNG_MINIMUM_CHUNKS:
        return False
    header_type, header_data = chunks[0]
    end_type, end_data = chunks[-1]
    if (
        header_type != b"IHDR"
        or len(header_data) != PNG_IHDR_LENGTH
        or end_type != b"IEND"
        or end_data
        or _uint(header_data[:4]) == 0
        or _uint(header_data[4:8]) == 0
    ):
        return False
    compressed_data = b"".join(
        chunk_data for kind, chunk_data in chunks if kind == b"IDAT"
    )
    if not compressed_data:
        return False
    try:
        _ = zlib.decompress(compressed_data)
    except zlib.error:
        return False
    return True


# JPEG SOI/EOI 마커와 SOF 프레임 세그먼트를 찾아 폭/높이가 유효한
# 프레임이 있는지 확인한다. readable_mime_type에서 호출된다.
def _jpeg_is_readable(data: bytes) -> bool:
    if not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9"):
        return False
    for marker in JPEG_FRAME_MARKERS:
        frame_start = data.find(bytes((JPEG_MARKER_PREFIX, marker)))
        if frame_start < 0 or frame_start + JPEG_FRAME_MINIMUM_LENGTH > len(data):
            continue
        segment_length = _uint(data[frame_start + 2 : frame_start + 4])
        if segment_length < JPEG_FRAME_MINIMUM_LENGTH:
            continue
        height = _uint(data[frame_start + 5 : frame_start + 7])
        width = _uint(data[frame_start + 7 : frame_start + 9])
        component_count = data[frame_start + 9]
        expected_length = JPEG_FRAME_MINIMUM_LENGTH + (
            JPEG_COMPONENT_SIZE * (component_count - 1)
        )
        if width > 0 and height > 0 and segment_length == expected_length:
            return True
    return False


# GIF 헤더 시그니처와 폭/높이, 이미지 디스크립터(",")와 트레일러(";")
# 존재 여부로 구조적 온전성을 확인한다. readable_mime_type에서 호출된다.
def _gif_is_readable(data: bytes) -> bool:
    if not data.startswith((b"GIF87a", b"GIF89a")) or len(data) < GIF_MINIMUM_LENGTH:
        return False
    width = int.from_bytes(data[6:8], byteorder="little")
    height = int.from_bytes(data[8:10], byteorder="little")
    return width > 0 and height > 0 and b"," in data[13:-1] and data.endswith(b";")


def readable_mime_type(data: bytes) -> str | None:
    """Return the supported MIME type only for structurally readable image data."""
    if _png_is_readable(data):
        return "image/png"
    if _jpeg_is_readable(data):
        return "image/jpeg"
    if _gif_is_readable(data):
        return "image/gif"
    return None
