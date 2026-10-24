"""Photographs : safe upload handling, perceptual hashing, duplicate detection, avatars.

Every uploaded image is opened with Pillow (so a renamed .exe / script is refused), limited in size,
rotated by its EXIF tag, cropped to a square, re-encoded as a clean JPEG (all metadata removed) and
stored under a random file name - never under the name the user chose.
"""
import hashlib
import io
import uuid
from pathlib import Path
from PIL import Image, ImageOps, UnidentifiedImageError
from flask import current_app
from . import db
from .db import AppError

Image.MAX_IMAGE_PIXELS = 40_000_000           # protects against decompression bombs
ALLOWED_FORMATS = {"JPEG", "PNG", "MPO", "WEBP"}
SIZE = 480


def _dhash(img):
    """64-bit difference hash -> 16 hex characters. Similar pictures give hashes that differ in few bits."""
    small = img.convert("L").resize((9, 8), Image.LANCZOS)
    px = list(small.getdata())
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (1 if px[row * 9 + col] > px[row * 9 + col + 1] else 0)
    return f"{bits:016x}"


def hamming(h1, h2):
    return bin(int(h1, 16) ^ int(h2, 16)).count("1")


def process_upload(file_storage):
    """Validate an uploaded picture. Returns dict(data=<jpeg bytes>, sha256, dhash). Raises AppError."""
    cfg = current_app.config
    if file_storage is None or not getattr(file_storage, "filename", ""):
        raise AppError("Please choose a photograph.")
    raw = file_storage.stream.read(cfg["PHOTO_MAX_BYTES"] + 1)
    if not raw:
        raise AppError("The selected file is empty.")
    if len(raw) > cfg["PHOTO_MAX_BYTES"]:
        raise AppError("The photograph is too large (maximum 3 MB).")
    try:
        probe = Image.open(io.BytesIO(raw))
        fmt = probe.format
        probe.verify()                                   # structural check
        img = Image.open(io.BytesIO(raw))
        img.load()
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError):
        raise AppError("That file is not a valid image. Upload a JPG or PNG photograph.")
    if fmt not in ALLOWED_FORMATS:
        raise AppError("Only JPG, PNG or WEBP photographs are accepted.")
    img = ImageOps.exif_transpose(img)
    if min(img.size) < cfg["PHOTO_MIN_PIXELS"]:
        raise AppError(f"The photograph is too small (at least {cfg['PHOTO_MIN_PIXELS']} x {cfg['PHOTO_MIN_PIXELS']} pixels).")
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        img = bg
    else:
        img = img.convert("RGB")
    img = ImageOps.fit(img, (SIZE, SIZE), method=Image.LANCZOS, centering=(0.5, 0.4))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=88, optimize=True)     # new file: no EXIF / GPS / comments survive
    data = out.getvalue()
    return dict(data=data, sha256=hashlib.sha256(data).hexdigest(), dhash=_dhash(img))


def find_duplicate(dhash, sha256, exclude_voter=None):
    """Return the voter row that already uses this (or a near-identical) photograph, else None."""
    max_dist = current_app.config["PHOTO_DUP_DISTANCE"]
    rows = db.query("SELECT VoterID, PhotoSha256, PhotoDHash FROM Voter "
                    "WHERE PhotoDHash IS NOT NULL OR PhotoSha256 IS NOT NULL")
    for r in rows:
        if exclude_voter and r["VoterID"] == exclude_voter:
            continue
        if r["PhotoSha256"] and r["PhotoSha256"] == sha256:
            return r
        if r["PhotoDHash"] and hamming(r["PhotoDHash"], dhash) <= max_dist:
            return r
    return None


# ---------------------------------------------------------------- storage
def voter_dir():
    d = Path(current_app.config["UPLOAD_DIR"]) / "voters"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_voter_photo(data):
    name = uuid.uuid4().hex + ".jpg"
    (voter_dir() / name).write_bytes(data)
    return name


def delete_voter_photo(name):
    if not name:
        return
    try:
        p = voter_dir() / Path(name).name
        if p.is_file():
            p.unlink()
    except OSError:
        pass


def voter_photo_path(name):
    if not name:
        return None
    p = voter_dir() / Path(name).name
    return p if p.is_file() else None


def candidate_photo_path(name):
    """Candidate pictures live in app/static/people (shipped with the project) or the upload folder."""
    if not name:
        return None
    safe = Path(name).name
    for base in (Path(current_app.config["STATIC_PEOPLE_DIR"]), Path(current_app.config["UPLOAD_DIR"]) / "candidates"):
        p = base / safe
        if p.is_file():
            return p
    return None


def save_candidate_photo(data):
    d = Path(current_app.config["UPLOAD_DIR"]) / "candidates"
    d.mkdir(parents=True, exist_ok=True)
    name = uuid.uuid4().hex + ".jpg"
    (d / name).write_bytes(data)
    return name


# ---------------------------------------------------------------- generated avatar (when there is no photograph)
_TONES = [("#e9d8c4", "#b5835a"), ("#d5e2d6", "#5d8a6a"), ("#f2d9d0", "#c0674a"),
          ("#dfe3ec", "#6f7fa6"), ("#efe3c2", "#b08d2c"), ("#e5d9e8", "#8a6a9a")]


def avatar_svg(seed_text):
    h = int(hashlib.sha256((seed_text or "?").encode("utf-8")).hexdigest()[:8], 16)
    bg, fg = _TONES[h % len(_TONES)]
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120" width="480" height="480">'
            f'<rect width="120" height="120" fill="{bg}"/>'
            f'<circle cx="60" cy="46" r="21" fill="{fg}"/>'
            f'<path d="M16 120c0-26 19-42 44-42s44 16 44 42z" fill="{fg}"/></svg>')
