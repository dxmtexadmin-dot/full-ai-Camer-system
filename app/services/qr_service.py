"""QR code generation, label rendering and print bookkeeping.

The QR payload contains *only* a random URL-safe token plus the site root:
``https://host/qr/<token>``. No inventory data, no credentials, no internal
database id - a photographed label cannot be used to learn anything about the
device beyond what the recipient of that label could read with their own eyes.

Labels are rendered server-side with Pillow at print resolution so every module
stays a whole number of pixels (no interpolation, therefore no distortion).
"""

from __future__ import annotations

import io
import logging
import math
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import qrcode
from PIL import Image, ImageDraw, ImageFont
from qrcode.constants import ERROR_CORRECT_Q
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AssetQrCode, EquipmentAsset
from app.models.user import utcnow

logger = logging.getLogger(__name__)

#: Standard label sizes in millimetres (width, height).
SIZE_PRESETS: dict[str, tuple[float, float]] = {
    "20x20": (20.0, 20.0),
    "25x25": (25.0, 25.0),
    "30x30": (30.0, 30.0),
    "40x40": (40.0, 40.0),
    "50x30": (50.0, 30.0),
    "60x40": (60.0, 40.0),
}
DEFAULT_SIZE = "30x30"
MIN_MM = 20.0  # below this the QR modules get too small for a phone camera
MAX_MM = 210.0  # A4 width; anything larger cannot be printed on one label
PRINT_DPI = 300
#: Smallest module edge on paper. 5 px at 300 dpi is ~0.42 mm; a phone camera
#: needs roughly 0.1 mm, so even the 20x20 mm label keeps a comfortable margin.
MIN_MODULE_PX = 5

MM_PER_INCH = 25.4

_FONT_CANDIDATES = (
    "arialbd.ttf",
    "arial.ttf",
    "segoeuib.ttf",
    "segoeui.ttf",
    "DejaVuSans-Bold.ttf",
    "DejaVuSans.ttf",
)


@dataclass
class LabelOptions:
    """Which pieces of information appear on a printed label."""

    show_qr: bool = True
    show_asset_code: bool = True
    show_name: bool = True
    show_model: bool = True
    show_responsible: bool = False
    show_org: bool = False
    show_logo: bool = False
    org_name: str = "DXM TEX"
    extra_text: str = ""
    logo_path: Optional[Path] = None

    @classmethod
    def from_form(cls, data: dict, logo_path: Optional[Path] = None) -> "LabelOptions":
        """Build options from raw (possibly unchecked) form values."""

        def on(key: str, default: bool = False) -> bool:
            raw = data.get(key)
            if raw is None:
                return default
            return str(raw).strip().lower() in {"1", "true", "on", "yes"}

        return cls(
            show_qr=on("show_qr", True),
            show_asset_code=on("show_asset_code", True),
            show_name=on("show_name", True),
            show_model=on("show_model", True),
            show_responsible=on("show_responsible"),
            show_org=on("show_org"),
            show_logo=on("show_logo"),
            org_name=str(data.get("org_name") or "DXM TEX").strip()[:80],
            extra_text=str(data.get("extra_text") or "").strip()[:120],
            logo_path=logo_path,
        )


# --------------------------------------------------------------------------- #
# Sizes
# --------------------------------------------------------------------------- #
def is_preset(size: str) -> bool:
    return str(size or "").strip().lower() in SIZE_PRESETS


def size_choices() -> list[tuple[str, str]]:
    """``(value, label)`` pairs for the size selector."""
    return [(key, f"{int(w)}x{int(h)} mm") for key, (w, h) in SIZE_PRESETS.items()]


def parse_label_size(value: Optional[str], width_mm=None, height_mm=None) -> tuple[float, float]:
    """Resolve a label size from a preset name or explicit millimetres.

    Raises ``ValueError`` with an Uzbek message the UI can show as-is.
    """
    if width_mm not in (None, "") and height_mm not in (None, ""):
        try:
            w = float(width_mm)
            h = float(height_mm)
        except (TypeError, ValueError) as exc:
            raise ValueError("O'lcham raqam bo'lishi kerak (mm).") from exc
        return _validate_size(w, h)

    key = str(value or DEFAULT_SIZE).strip().lower()
    if key in SIZE_PRESETS:
        return SIZE_PRESETS[key]
    # Accept "30x30" style input even if it is not a preset.
    if "x" in key:
        left, _, right = key.partition("x")
        try:
            return _validate_size(float(left), float(right))
        except ValueError as exc:
            raise ValueError("O'lcham formati: 30x30 (mm).") from exc
    raise ValueError("Noma'lum o'lcham. Ro'yxatdan tanlang yoki WxH kiriting.")


def _validate_size(width_mm: float, height_mm: float) -> tuple[float, float]:
    if not (MIN_MM <= width_mm <= MAX_MM and MIN_MM <= height_mm <= MAX_MM):
        raise ValueError(
            f"O'lcham {int(MIN_MM)}..{int(MAX_MM)} mm oralig'ida bo'lishi kerak "
            f"({int(MIN_MM)} mm dan kichik yorliqdagi QR o'qilmasligi mumkin)."
        )
    return round(width_mm, 1), round(height_mm, 1)


def size_key(width_mm: float, height_mm: float) -> str:
    """Filament name for a size: preset key when it matches, else ``WxH``."""
    for key, (w, h) in SIZE_PRESETS.items():
        if abs(w - width_mm) < 0.05 and abs(h - height_mm) < 0.05:
            return key
    return f"{width_mm:g}x{height_mm:g}"


def mm_to_px(value_mm: float, dpi: int = PRINT_DPI) -> int:
    return max(1, int(round(value_mm * dpi / MM_PER_INCH)))


# --------------------------------------------------------------------------- #
# Token + record
# --------------------------------------------------------------------------- #
def new_token() -> str:
    return secrets.token_hex(16)  # 32 url-safe hex chars, not guessable


def ensure_qr_code(
    session: Session,
    asset: EquipmentAsset,
    *,
    user=None,
    label_size: str = DEFAULT_SIZE,
) -> AssetQrCode:
    """Return the active QR record of ``asset``, creating it on first use."""
    existing = session.scalar(select(AssetQrCode).where(AssetQrCode.asset_id == asset.id))
    if existing is not None:
        if not existing.is_active:
            existing.is_active = True
            session.flush()
        return existing

    token = new_token()
    while session.scalar(select(AssetQrCode).where(AssetQrCode.token == token)) is not None:
        token = new_token()

    record = AssetQrCode(
        asset_id=asset.id,
        token=token,
        label_size=size_key(*parse_label_size(label_size)),
        issued_by_user_id=getattr(user, "id", None),
    )
    session.add(record)
    session.flush()
    logger.info("QR issued for asset %s", asset.asset_code)
    return record


def reissue_qr_code(session: Session, asset: EquipmentAsset, *, user=None) -> AssetQrCode:
    """Replace the token (lost/stolen label). The old token stops resolving."""
    record = session.scalar(select(AssetQrCode).where(AssetQrCode.asset_id == asset.id))
    token = new_token()
    while session.scalar(select(AssetQrCode).where(AssetQrCode.token == token)) is not None:
        token = new_token()
    if record is None:
        record = AssetQrCode(
            asset_id=asset.id,
            token=token,
            issued_by_user_id=getattr(user, "id", None),
        )
        session.add(record)
    else:
        record.token = token
        record.issued_at = utcnow()
        record.issued_by_user_id = getattr(user, "id", None)
        record.is_active = True
    session.flush()
    return record


def find_by_token(session: Session, token: str) -> Optional[EquipmentAsset]:
    """Resolve a scanned token to its asset; ``None`` for unknown/disabled ones."""
    clean = (token or "").strip()
    if not clean or len(clean) > 64:
        return None
    record = session.scalar(select(AssetQrCode).where(AssetQrCode.token == clean))
    if record is None or not record.is_active:
        return None
    return record.asset


def record_print(session: Session, qr: AssetQrCode, *, label_size: Optional[str] = None) -> AssetQrCode:
    qr.reprint_count = int(qr.reprint_count or 0) + 1
    qr.last_printed_at = utcnow()
    if label_size:
        qr.label_size = label_size
    session.flush()
    return qr


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def qr_payload(base_url: str, token: str) -> str:
    return f"{(base_url or '').rstrip('/')}/qr/{token}"


def render_qr_image(payload: str, *, target_px: int, quiet_zone: int = 2) -> Image.Image:
    """Render a QR whose modules are a whole number of pixels (never blurred)."""
    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_Q, border=quiet_zone)
    qr.add_data(payload)
    qr.make(fit=True)
    modules = qr.modules_count + quiet_zone * 2
    box = max(1, int(target_px // modules))
    qr.box_size = box
    image = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    # Guarantee square pixels: re-render once at the exact module grid.
    expected = modules * box
    if image.size != (expected, expected):
        image = image.resize((expected, expected), Image.NEAREST)
    return image


def render_qr_png(payload: str, *, target_px: int = 600, quiet_zone: int = 4) -> bytes:
    """Standalone PNG for downloading / embedding."""
    image = render_qr_image(payload, target_px=target_px, quiet_zone=quiet_zone)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def qr_min_side(payload: str, *, quiet_zone: int = 2) -> int:
    """Smallest QR width in pixels that still has ``MIN_MODULE_PX`` modules.

    At 300 dpi one module of ``MIN_MODULE_PX`` pixels is ~0.34 mm, well above
    the ~0.1 mm a phone camera needs, so a code drawn at this size reads
    reliably from a printed 20x20 mm label.
    """
    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_Q, border=quiet_zone)
    qr.add_data(payload)
    qr.make(fit=True)
    return (qr.modules_count + quiet_zone * 2) * MIN_MODULE_PX


def _load_font(px: int) -> ImageFont.ImageFont:
    size = max(8, int(px))
    for name in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)  # Pillow >= 10.1
    except TypeError:  # pragma: no cover - very old Pillow
        return ImageFont.load_default()


def _fit_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
    if not text:
        return ""
    if draw.textlength(text, font=font) <= max_width:
        return text
    ellipsis = "…"
    while text and draw.textlength(text + ellipsis, font=font) > max_width:
        text = text[:-1]
    return (text + ellipsis) if text else ""


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int, max_lines: int) -> list[str]:
    words = str(text or "").split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=font) <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = word
        if len(lines) >= max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
    if lines:
        lines[-1] = _fit_text(draw, lines[-1], font, max_width)
    return lines


def render_label_png(
    asset: EquipmentAsset,
    qr_token: str,
    *,
    base_url: str,
    size: tuple[float, float] = SIZE_PRESETS[DEFAULT_SIZE],
    options: Optional[LabelOptions] = None,
    dpi: int = PRINT_DPI,
) -> bytes:
    """Full printable label (QR + captions) as PNG at print resolution."""
    options = options or LabelOptions()
    width_mm, height_mm = size
    width_px, height_px = mm_to_px(width_mm, dpi), mm_to_px(height_mm, dpi)
    pad = mm_to_px(1.2, dpi)

    canvas = Image.new("RGB", (width_px, height_px), "white")
    draw = ImageDraw.Draw(canvas)

    text_lines: list[tuple[str, float]] = []  # (text, relative size)
    if options.show_org and options.org_name:
        text_lines.append((options.org_name, 0.16))
    if options.show_asset_code:
        text_lines.append((asset.asset_code, 0.26))
    if options.show_name:
        text_lines.append((asset.display_name, 0.20))
    if options.show_model and asset.full_model:
        text_lines.append((asset.full_model, 0.15))
    if options.show_responsible and asset.responsible_name:
        text_lines.append((asset.responsible_name, 0.14))
    if options.extra_text:
        text_lines.append((options.extra_text, 0.12))

    has_text = bool(text_lines)
    # A wide label puts the code on the left and the text on the right; square
    # and portrait labels stack them, which keeps the code much larger.
    landscape = width_px > height_px * 1.15

    logo_box = 0
    logo = None
    if options.show_logo and options.logo_path and Path(options.logo_path).exists():
        try:
            logo = Image.open(options.logo_path).convert("RGB")
            logo_box = mm_to_px(min(8.0, height_mm / 4), dpi)
            ratio = logo_box / max(1, logo.width)
            logo = logo.resize((logo_box, max(1, int(logo.height * ratio))), Image.LANCZOS)
            logo_box = logo.height
        except Exception:  # pragma: no cover - unreadable logo must not break printing
            logger.warning("Logo could not be loaded: %s", options.logo_path)
            logo = None
            logo_box = 0

    header = logo_box + (mm_to_px(0.8, dpi) if logo else 0)

    def _qr_slot(side_by_side: bool, with_text: bool) -> int:
        """Square side the layout would give the QR code."""
        if side_by_side and with_text:
            return min(height_px - 2 * pad, int(width_px * 0.52))
        if with_text:
            return min(width_px - 2 * pad, int((height_px - 2 * pad - header) * 0.68))
        return min(width_px - 2 * pad, height_px - 2 * pad - header)

    payload = qr_payload(base_url, qr_token)
    if options.show_qr and has_text:
        # On a small label the captions would shrink the code below the readable
        # module size. An unreadable label is useless, so the code wins.
        needed = qr_min_side(payload)
        if _qr_slot(landscape, True) < needed and _qr_slot(landscape, False) >= needed:
            has_text = False

    if options.show_qr:
        qr_side = _qr_slot(landscape, has_text)
        if landscape and has_text:
            text_width = width_px - qr_side - 3 * pad
            qr_x, qr_y = pad, (height_px - qr_side) // 2
            text_x, text_y = pad + qr_side + pad, pad + header
            text_box_h = height_px - 2 * pad - header
        else:
            qr_x, qr_y = (width_px - qr_side) // 2, pad + header
            text_x, text_y = pad, qr_y + qr_side + mm_to_px(0.6, dpi)
            text_width = width_px - 2 * pad
            text_box_h = height_px - text_y - pad

        qr_image = render_qr_image(payload, target_px=qr_side, quiet_zone=2)
        # Centre the square QR in its slot without scaling it.
        canvas.paste(qr_image, (qr_x + (qr_side - qr_image.width) // 2, qr_y))
    else:
        text_x, text_y = pad, pad + header
        text_width, text_box_h = width_px - 2 * pad, height_px - 2 * pad - header

    if logo is not None:
        canvas.paste(logo, (pad, pad))

    if has_text and text_width > 8 and text_box_h > 8:
        base = min(text_width, text_box_h)
        sizes = [max(9, int(base * rel)) for _, rel in text_lines]
        total = sum(sizes) + int(len(sizes) * mm_to_px(0.4, dpi))
        if total > text_box_h:
            scale = text_box_h / total
            sizes = [max(8, int(value * scale)) for value in sizes]
        cursor = text_y
        for (text, _rel), px in zip(text_lines, sizes):
            font = _load_font(px)
            line_height = int(px * 1.15)
            wrapped = _wrap(draw, text, font, text_width, max_lines=2)
            for line in wrapped:
                if cursor + line_height > height_px - pad:
                    break
                draw.text((text_x, cursor), line, fill="black", font=font)
                cursor += line_height

    draw.rectangle([0, 0, width_px - 1, height_px - 1], outline=(190, 190, 190))
    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG", dpi=(dpi, dpi), optimize=True)
    return buffer.getvalue()


def a4_label_capacity(size: tuple[float, float], *, margin_mm: float = 10.0, gap_mm: float = 3.0) -> tuple[int, int]:
    """How many labels of ``size`` fit on A4 (portrait), and the columns/rows."""
    usable_w = 210.0 - 2 * margin_mm
    usable_h = 297.0 - 2 * margin_mm
    width_mm, height_mm = size
    cols = max(0, int((usable_w + gap_mm) // (width_mm + gap_mm)))
    rows = max(0, int((usable_h + gap_mm) // (height_mm + gap_mm)))
    return cols, rows
