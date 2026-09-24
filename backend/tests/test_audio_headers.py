import struct

import pytest

from backend.app.core.errors import AppError
from backend.app.services.assets import inspect_wav, normalize_cosyvoice_wav
from backend.app.services.providers import wav_tone


def streaming_header(audio):
    data = bytearray(audio)
    struct.pack_into("<I", data, 4, 0x7FFFFFBF)
    struct.pack_into("<I", data, 40, 0x7FFFFF9B)
    return bytes(data)


def test_completed_streaming_header_finalized_without_changing_audio():
    original = wav_tone(3.89)
    streamed = streaming_header(original)
    with pytest.raises(AppError):
        inspect_wav(streamed)  # Upload validation remains strict.
    fixed = normalize_cosyvoice_wav(streamed)
    assert fixed == original
    assert fixed[44:] == streamed[44:]
    assert inspect_wav(fixed) == pytest.approx(3890)
    assert normalize_cosyvoice_wav(fixed) == fixed


def test_normalization_does_not_repair_ordinary_truncation():
    truncated = wav_tone()[:-100]
    assert normalize_cosyvoice_wav(truncated) == truncated
    with pytest.raises(AppError):
        inspect_wav(normalize_cosyvoice_wav(truncated))


def test_streaming_header_incomplete_frame_is_rejected():
    with pytest.raises(AppError):
        normalize_cosyvoice_wav(streaming_header(wav_tone())[:-1])
