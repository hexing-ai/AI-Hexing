import hashlib
import io
import os
import struct
import time
import wave
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

from backend.app.core.errors import AppError
from backend.app.models import Asset, uid


def normalize_cosyvoice_wav(data):
    """Finalize the observed CosyVoice streaming header after SDK completion.

    Only this exact PCM header and placeholder size pair are accepted. User
    uploads and ordinary truncated WAV files must retain strict validation.
    Audio frames are preserved byte-for-byte; no resampling or synthesis.
    """
    if len(data) < 44:
        return data
    if not (
        data[:4] == b"RIFF"
        and data[8:12] == b"WAVE"
        and data[12:16] == b"fmt "
        and data[36:40] == b"data"
        and struct.unpack_from("<I", data, 4)[0] == 0x7FFFFFBF
        and struct.unpack_from("<I", data, 40)[0] == 0x7FFFFF9B
        and struct.unpack_from("<IHHIIHH", data, 16) == (16, 1, 1, 24000, 48000, 2, 16)
    ):
        return data
    frame_bytes = len(data) - 44
    if frame_bytes <= 0 or frame_bytes % 2:
        raise AppError("AUDIO_RESULT_INVALID", "返回音频的采样帧不完整，已保留原始结果，请勿重复合成。", 422)
    finalized = bytearray(data)
    struct.pack_into("<I", finalized, 4, len(data) - 8)
    struct.pack_into("<I", finalized, 40, frame_bytes)
    return bytes(finalized)


def validate_sample_url_expiry(url):
    """Reject expired OSS V1/V4 links without exposing their signature."""
    query = parse_qs(urlsplit(url).query, keep_blank_values=True)

    def value(key):
        values = query.get(key, [])
        if len(values) != 1 or not values[0]:
            raise ValueError()
        return values[0]

    try:
        if "Expires" in query:
            expires_at = int(value("Expires"))
        elif "x-oss-date" in query or "x-oss-expires" in query:
            signed_at = datetime.strptime(value("x-oss-date"), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            expires_at = signed_at.timestamp() + int(value("x-oss-expires"))
        else:
            # Unknown signing formats still require successful OSS access and
            # byte-for-byte sample verification before enrollment.
            return
    except (ValueError, OverflowError):
        raise AppError(
            "SAMPLE_URL_INVALID", "录音链接的有效期参数不正确，请从 OSS 重新生成完整签名 URL。", 422
        )
    if expires_at <= time.time():
        raise AppError(
            "SAMPLE_URL_EXPIRED",
            "OSS 录音签名链接已过期。请为原文件重新生成签名 URL，并在样本面板重新绑定；无需重新上传录音。",
            422,
        )


def inspect_wav(data, *, sample=False):
    try:
        if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
            raise ValueError()
        with wave.open(io.BytesIO(data), "rb") as w:
            channels, width, rate, count = (
                w.getnchannels(),
                w.getsampwidth(),
                w.getframerate(),
                w.getnframes(),
            )
            if (
                w.getcomptype() != "NONE"
                or channels not in (1, 2)
                or width != 2
                or rate < 16000
                or count <= 0
            ):
                raise ValueError()
            frames = w.readframes(count)
            if len(frames) != count * channels * width:
                raise ValueError()
            duration = count * 1000 / rate
            if sample and not 5000 <= duration <= 60000:
                raise AppError("AUDIO_DURATION_INVALID", "样本需为 5–60 秒，推荐 10–20 秒清晰录音。", 422)
            return duration
    except (wave.Error, ValueError, EOFError):
        raise AppError(
            "AUDIO_FORMAT_INVALID", "请使用完整的 16-bit PCM WAV，采样率至少 16kHz、单声道或双声道。", 415
        )


def asset_path(settings, key):
    root = settings.data_dir.resolve()
    path = root / key
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise AppError("ASSET_PATH_INVALID", "文件不可访问。", 404)
    return path


def save_asset(db, settings, owner, mode, data, kind, cleanup_after=None, consent_id=None):
    duration = inspect_wav(data, sample=kind == "voice_sample")
    return save_blob(
        db, settings, owner, mode, data, kind, "audio/wav", ".wav", duration, cleanup_after, consent_id
    )


def save_blob(
    db, settings, owner, mode, data, kind, mime, extension, duration=None, cleanup_after=None, consent_id=None
):
    key = f"private/{mode}/{uid()}{extension}"
    path = asset_path(settings, key)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(".part")
    with temporary.open("xb") as out:
        os.chmod(temporary, 0o600)
        out.write(data)
        out.flush()
        os.fsync(out.fileno())
    os.replace(temporary, path)
    asset = Asset(
        owner_id=owner,
        data_mode=mode,
        kind=kind,
        storage_key=key,
        sha256=hashlib.sha256(data).hexdigest(),
        mime=mime,
        byte_size=len(data),
        duration_ms=duration,
        cleanup_after=cleanup_after,
        consent_record_id=consent_id,
    )
    db.add(asset)
    db.flush()
    return asset


def delete_asset_file(db, settings, asset):
    asset_path(settings, asset.storage_key).unlink(missing_ok=True)
    asset.state = "deleted"
