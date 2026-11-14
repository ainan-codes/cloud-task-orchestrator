"""
snapchat_media.py — validate, encrypt and chunk media for the Snapchat
Public Profile API.

Snap does not accept a plain file. The documented flow is:

    key = openssl rand -hex 32                     # 32 raw bytes
    iv  = openssl rand -hex 16                     # 16 raw bytes
    openssl enc -aes-256-cbc -nosalt -e -K $key -iv $iv -in v.mp4 -out v.enc
    key64 = `echo $key | xxd -r -p | base64`       # base64 of the RAW bytes
    iv64  = `echo $iv  | xxd -r -p | base64`
    split -b 32m v.enc                             # <= 32 MiB per part

key64/iv64 go to Create Media; the parts go to the multipart upload.
Because `xxd -r -p` turns the hex back into bytes before base64, what Snap
receives is base64 of the 32 raw key bytes — not base64 of the hex text.

Validation runs before the upload, not after. Snap only reports "your video
is 4 seconds long" at the publish step, which is several minutes and a whole
file transfer too late, and the media container it wasted expires in 24h.
Neither Vercel's serverless runtime nor the GitHub Actions job has ffmpeg,
so the checks below read the MP4 box tree directly.
"""

import base64
import secrets

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from snapchat_errors import SnapMediaError

# --- Snap's published media requirements -------------------------------
# developers.snap.com/api/marketing-api/Public-Profile-API/ProfileAssetManagement
SPOTLIGHT_MIN_SECONDS = 6.0
SPOTLIGHT_MAX_SECONDS = 60.0
STORY_MIN_SECONDS = 5.0
STORY_MAX_SECONDS = 60.0
MIN_WIDTH = 540
MIN_HEIGHT = 960

# Snapchat's Monetization Program pays on Spotlight videos only from this
# length up ("for Spotlight videos to be eligible to earn revenue, they must
# be at least 30 seconds long"). It is NOT an API limit — Snap accepts a 6
# second Spotlight happily, it just cannot earn. Surfaced as an advisory so a
# creator is not left wondering why published posts never generate revenue.
MONETIZATION_MIN_SPOTLIGHT_SECONDS = 30.0

# --- Snap's published upload limits ------------------------------------
CHUNK_BYTES = 32 * 1024 * 1024   # "split -b 32m"
MAX_PARTS = 35                   # part_number is documented as 1..35
MAX_UPLOAD_BYTES = 1024 * 1024 * 1024

# Duration probing tolerates a little rounding: a "6.00s" export routinely
# lands at 5.98s in the container's own timescale, and Snap accepts it.
DURATION_TOLERANCE_SECONDS = 0.05

_MP4_BRANDS = {
    b"isom", b"iso2", b"iso4", b"iso5", b"iso6", b"mp41", b"mp42",
    b"avc1", b"M4V ", b"dash", b"mmp4",
}


# ============================ MP4 box reader ============================
def _iter_boxes(data, start, end):
    """Yield (type, payload_start, box_end) for each box in [start, end)."""
    off = start
    while off + 8 <= end:
        size = int.from_bytes(data[off:off + 4], "big")
        btype = bytes(data[off + 4:off + 8])
        header = 8
        if size == 1:                      # 64-bit extended size
            if off + 16 > end:
                return
            size = int.from_bytes(data[off + 8:off + 16], "big")
            header = 16
        elif size == 0:                    # box runs to the end of its parent
            size = end - off
        if size < header or off + size > end:
            return
        yield btype, off + header, off + size
        off += size


def _find_box(data, start, end, wanted):
    for btype, pstart, bend in _iter_boxes(data, start, end):
        if btype == wanted:
            return pstart, bend
    return None, None


def _s1616(raw):
    """Read a signed 16.16 fixed-point number."""
    v = int.from_bytes(raw, "big")
    if v >= 0x80000000:
        v -= 0x100000000
    return v / 65536.0


def _u1616(raw):
    return int.from_bytes(raw, "big") / 65536.0


def _read_mvhd(data, pstart, bend):
    """Return the movie duration in seconds from an mvhd payload."""
    if pstart + 4 > bend:
        return None
    version = data[pstart]
    if version == 1:
        ts_off, dur_off, dur_len = pstart + 20, pstart + 24, 8
    else:
        ts_off, dur_off, dur_len = pstart + 12, pstart + 16, 4
    if dur_off + dur_len > bend:
        return None
    timescale = int.from_bytes(data[ts_off:ts_off + 4], "big")
    duration = int.from_bytes(data[dur_off:dur_off + dur_len], "big")
    if not timescale:
        return None
    return duration / timescale


def _read_tkhd(data, pstart, bend):
    """Return (width, height) from a tkhd payload.

    tkhd stores the display size, so a portrait clip shot on a phone and
    stored as a rotated 1920x1080 track only reports 1080x1920 once the
    track matrix is applied — which is what the rotation check below does.
    """
    if pstart + 4 > bend:
        return None, None
    version = data[pstart]
    body = pstart + 4 + (32 if version == 1 else 20)
    matrix_off = body + 16
    w_off, h_off = body + 52, body + 56
    if h_off + 4 > bend:
        return None, None

    width = _u1616(data[w_off:w_off + 4])
    height = _u1616(data[h_off:h_off + 4])

    # Matrix {a b u; c d v; x y w} — a 90/270 rotation has a == 0, |b| == 1.
    a = _s1616(data[matrix_off:matrix_off + 4])
    b = _s1616(data[matrix_off + 4:matrix_off + 8])
    if abs(a) < 0.01 and abs(b) >= 0.99:
        width, height = height, width

    return int(round(width)), int(round(height))


def probe_mp4(data):
    """Read format, duration and display resolution straight out of the file.

    Returns {"brand", "duration_seconds", "width", "height"}.
    Raises SnapMediaError if this is not a readable MP4.
    """
    if not data or len(data) < 32:
        raise SnapMediaError("Media file is empty or truncated.")

    end = len(data)
    brand = None
    moov = None

    for btype, pstart, bend in _iter_boxes(data, 0, end):
        if btype == b"ftyp" and brand is None:
            brand = bytes(data[pstart:pstart + 4])
        elif btype == b"moov":
            moov = (pstart, bend)

    if brand is None:
        raise SnapMediaError(
            "File is not an MP4 - no 'ftyp' box found. Snapchat accepts .mp4 only."
        )
    if brand not in _MP4_BRANDS:
        raise SnapMediaError(
            f"File declares brand '{brand.decode(errors='replace')}', which is not an "
            "MP4 brand Snapchat accepts. Re-export as H.264/AAC .mp4."
        )
    if moov is None:
        raise SnapMediaError(
            "MP4 has no 'moov' box - the file is incomplete or still uploading."
        )

    moov_start, moov_end = moov
    duration = None
    mv_start, mv_end = _find_box(data, moov_start, moov_end, b"mvhd")
    if mv_start is not None:
        duration = _read_mvhd(data, mv_start, mv_end)

    # Take the resolution from the first track carrying a video handler.
    width = height = None
    for btype, tstart, tend in _iter_boxes(data, moov_start, moov_end):
        if btype != b"trak":
            continue
        mdia_s, mdia_e = _find_box(data, tstart, tend, b"mdia")
        is_video = False
        if mdia_s is not None:
            hdlr_s, hdlr_e = _find_box(data, mdia_s, mdia_e, b"hdlr")
            if hdlr_s is not None and hdlr_s + 12 <= hdlr_e:
                is_video = bytes(data[hdlr_s + 8:hdlr_s + 12]) == b"vide"
        if not is_video:
            continue
        tk_s, tk_e = _find_box(data, tstart, tend, b"tkhd")
        if tk_s is not None:
            width, height = _read_tkhd(data, tk_s, tk_e)
        break

    if duration is None:
        raise SnapMediaError("Could not read the video duration from the MP4 header.")
    if not width or not height:
        raise SnapMediaError("Could not read the video resolution from the MP4 header.")

    return {
        "brand": brand.decode(errors="replace").strip(),
        "duration_seconds": round(duration, 3),
        "width": width,
        "height": height,
    }


# ============================== validation =============================
def duration_bounds(placement):
    """Snap's duration window for a placement, in seconds."""
    if placement == "story":
        return STORY_MIN_SECONDS, STORY_MAX_SECONDS
    return SPOTLIGHT_MIN_SECONDS, SPOTLIGHT_MAX_SECONDS


def validate(data, placement="spotlight", file_name=None):
    """Check the file against Snap's requirements for `placement`.

    Returns the probe dict on success; raises SnapMediaError with a message
    that names the actual value, so the dashboard can show the creator what
    to fix instead of "invalid media".
    """
    if file_name and not str(file_name).lower().endswith(".mp4"):
        raise SnapMediaError(
            f"'{file_name}' is not a .mp4 file. Snapchat accepts .mp4 only."
        )

    size = len(data or b"")
    if size > MAX_UPLOAD_BYTES:
        raise SnapMediaError(
            f"File is {size / 1024 / 1024:.0f} MB; Snapchat's upload limit is 1 GB."
        )

    info = probe_mp4(data)
    lo, hi = duration_bounds(placement)
    dur = info["duration_seconds"]

    if dur < lo - DURATION_TOLERANCE_SECONDS:
        raise SnapMediaError(
            f"Video is {dur:.2f}s. Snapchat {placement} requires at least {lo:g}s."
        )
    if dur > hi + DURATION_TOLERANCE_SECONDS:
        raise SnapMediaError(
            f"Video is {dur:.2f}s. Snapchat {placement} allows at most {hi:g}s."
        )
    if info["width"] < MIN_WIDTH or info["height"] < MIN_HEIGHT:
        raise SnapMediaError(
            f"Video is {info['width']}x{info['height']}. Snapchat requires at least "
            f"{MIN_WIDTH}x{MIN_HEIGHT} (portrait 9:16)."
        )

    info["placement"] = placement
    info["size_bytes"] = size

    # Advisory, never a rejection: a 12-second Spotlight publishes fine, it
    # simply cannot earn under Snap's Monetization Program.
    info["monetizable_length"] = (
        placement == "spotlight" and dur >= MONETIZATION_MIN_SPOTLIGHT_SECONDS
    )
    if placement == "spotlight" and not info["monetizable_length"]:
        info["monetization_note"] = (
            f"{dur:.1f}s publishes to Spotlight, but Snapchat's Monetization "
            f"Program only pays on Spotlight videos of at least "
            f"{MONETIZATION_MIN_SPOTLIGHT_SECONDS:g}s."
        )
    return info


# ============================== encryption =============================
def generate_key_iv():
    """A fresh AES-256-CBC key and IV, as raw bytes."""
    return secrets.token_bytes(32), secrets.token_bytes(16)


def encrypt_media(data, key=None, iv=None):
    """AES-256-CBC encrypt with PKCS#7 padding — `openssl enc -aes-256-cbc -nosalt`.

    Returns (ciphertext, key_b64, iv_b64), where the base64 values are what
    Create Media expects for its `key` and `iv` fields.
    """
    if key is None or iv is None:
        key, iv = generate_key_iv()
    if len(key) != 32:
        raise SnapMediaError("AES key must be exactly 32 bytes (AES-256).")
    if len(iv) != 16:
        raise SnapMediaError("AES IV must be exactly 16 bytes.")

    padder = padding.PKCS7(algorithms.AES.block_size).padder()
    padded = padder.update(data) + padder.finalize()

    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()

    return (
        ciphertext,
        base64.b64encode(key).decode("ascii"),
        base64.b64encode(iv).decode("ascii"),
    )


def chunk_media(ciphertext, chunk_bytes=None):
    """Split the ciphertext into the parts the multipart upload takes.

    Returns [(part_number, bytes), ...] with part_number starting at 1.

    The default is read at call time rather than bound as a default argument,
    so CHUNK_BYTES stays adjustable — a smaller part size is how a memory-
    constrained runtime gets through a large file.
    """
    chunk_bytes = chunk_bytes or CHUNK_BYTES
    if not ciphertext:
        raise SnapMediaError("Nothing to upload - the encrypted media is empty.")

    parts = [
        (i + 1, ciphertext[off:off + chunk_bytes])
        for i, off in enumerate(range(0, len(ciphertext), chunk_bytes))
    ]
    if len(parts) > MAX_PARTS:
        raise SnapMediaError(
            f"File needs {len(parts)} upload parts; Snapchat allows {MAX_PARTS} "
            f"({MAX_PARTS * chunk_bytes // (1024 * 1024)} MB at "
            f"{chunk_bytes // (1024 * 1024)} MB per part)."
        )
    return parts


def prepare(data, placement="spotlight", file_name=None):
    """Validate, encrypt and chunk in one step — what the publisher calls.

    Returns {"info", "key_b64", "iv_b64", "parts", "encrypted_bytes"}.
    """
    info = validate(data, placement=placement, file_name=file_name)
    ciphertext, key_b64, iv_b64 = encrypt_media(data)
    parts = chunk_media(ciphertext)
    return {
        "info": info,
        "key_b64": key_b64,
        "iv_b64": iv_b64,
        "parts": parts,
        "encrypted_bytes": len(ciphertext),
    }
