import math
import struct
import wave
from pathlib import Path

SAMPLE_RATE = 16000
SAMPLES_PER_PACKET = 320
HEADER = 7
AUDIO_PACKET = HEADER + SAMPLES_PER_PACKET // 2
TYPE_AUDIO = 2

STEPS = [
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45, 50, 55, 60, 66, 73, 80, 88, 97,
    107, 118, 130, 143, 157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658, 724, 796,
    876, 963, 1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066, 2272, 2499, 2749, 3024, 3327, 3660, 4026, 4428, 4871,
    5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442, 11487, 12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623,
    27086, 29794, 32767,
]
INDEX_STEP = [-1, -1, -1, -1, 2, 4, 6, 8] * 2


def header(packet: bytes) -> dict:
    kind, clip, sequence, predictor, index = struct.unpack_from("<BBHhB", packet)
    return {"type": kind, "clip": clip, "sequence": sequence, "predictor": predictor, "index": index}


def decode(packet: bytes) -> list[int]:
    fields = header(packet)
    predictor, index = fields["predictor"], fields["index"]
    out = []
    for byte in packet[HEADER:]:
        for code in (byte & 0x0F, byte >> 4):
            step = STEPS[index]
            diff = step >> 3
            if code & 4:
                diff += step
            if code & 2:
                diff += step >> 1
            if code & 1:
                diff += step >> 2
            predictor = max(-32768, min(32767, predictor - diff if code & 8 else predictor + diff))
            index = max(0, min(88, index + INDEX_STEP[code]))
            out.append(predictor)
    return out


def rms(samples: list[int], remove_mean: bool = False) -> float:
    mean = sum(samples) / len(samples) if remove_mean else 0.0
    return math.sqrt(sum((s - mean) ** 2 for s in samples) / len(samples))


def write_wav(path: Path, samples: list[int]) -> None:
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        out.writeframes(struct.pack(f"<{len(samples)}h", *samples))
