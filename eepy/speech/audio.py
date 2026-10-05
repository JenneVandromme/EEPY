"""How long is a clip? Needed because nothing reports when playback ENDS.

The robot's daemon starts a sound and returns; it never says when it finished.
So the speaker works out each clip's length from the file itself and treats the
clip as finished after that long. WAV is exact. MP3 (only the Google engine
makes those) is estimated from its first frame's bitrate -- exact for the
constant-bitrate files Google returns.
"""

from __future__ import annotations

import io
import wave
from pathlib import Path

_MP3_BITRATES = {  # MPEG-1 Layer III, kbit/s, by index
    1: 32, 2: 40, 3: 48, 4: 56, 5: 64, 6: 80, 7: 96, 8: 112,
    9: 128, 10: 160, 11: 192, 12: 224, 13: 256, 14: 320}
_MP3_BITRATES_V2 = {  # MPEG-2 / 2.5 Layer III
    1: 8, 2: 16, 3: 24, 4: 32, 5: 40, 6: 48, 7: 56, 8: 64,
    9: 80, 10: 96, 11: 112, 12: 128, 13: 144, 14: 160}


def duration(data: bytes) -> float:
    """Seconds of audio in a WAV or MP3 clip. 0.0 if it cannot tell."""
    if data[:4] == b"RIFF":
        try:
            with wave.open(io.BytesIO(data)) as clip:
                return clip.getnframes() / float(clip.getframerate())
        except (wave.Error, EOFError):
            return 0.0
    return _mp3_duration(data)


def file_duration(path: Path) -> float:
    return duration(Path(path).read_bytes())


def _mp3_duration(data: bytes) -> float:
    start = 0
    if data[:3] == b"ID3" and len(data) > 10:          # skip an ID3v2 tag
        size = (data[6] << 21) | (data[7] << 14) | (data[8] << 7) | data[9]
        start = 10 + size
    for i in range(start, min(len(data) - 4, start + 4096)):
        if data[i] == 0xFF and data[i + 1] & 0xE0 == 0xE0:
            version = (data[i + 1] >> 3) & 0x3          # 3 = MPEG-1
            bitrate_index = (data[i + 2] >> 4) & 0xF
            table = _MP3_BITRATES if version == 3 else _MP3_BITRATES_V2
            kbps = table.get(bitrate_index)
            if kbps:
                return (len(data) - i) * 8 / (kbps * 1000.0)
    return 0.0


def kind(data: bytes) -> str:
    """'wav' or 'mp3'."""
    return "wav" if data[:4] == b"RIFF" else "mp3"
