"""Branded cards (1080x1350, Instagram 4:5). Our own design; a background photo is used only when\nphotos.py found one with a safe licence, and it is always credited on the card."""
import io
import os
from datetime import datetime, timedelta, timezone

from PIL import Image, ImageDraw, ImageFont

from . import config

W, H = 1080, 1350
IST = timezone(timedelta(hours=5, minutes=30))

CATEGORY_COLORS = {
    "Politics": "#E4572E", "Government & Policy": "#3A86FF", "Economy & Business": "#2A9D8F",
    "Crime & Law": "#B5179E", "Courts": "#7209B7", "Weather & Disaster": "#F77F00",
    "Health": "#06A77D", "Education": "#4361EE", "Science & Tech": "#00B4D8",
    "Sports": "#80B918", "Entertainment": "#F72585", "Environment": "#52B788",
    "Infrastructure & Transport": "#6C757D", "International": "#9D4EDD",
    "Society": "#FF9F1C", "Agriculture": "#8AB17D", "Defence": "#577590",
    "Fact Check": "#FFB703",
}

_FONT_DIRS = [
    "/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/dejavu",
    "/usr/share/fonts/truetype/liberation", "/Library/Fonts", "C:/Windows/Fonts",
]


def _font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    names = (["DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf", "Arial Bold.ttf", "arialbd.ttf"]
             if bold else ["DejaVuSans.ttf", "LiberationSans-Regular.ttf", "Arial.ttf", "arial.ttf"])
    for d in _FONT_DIRS:
        for n in names:
            p = os.path.join(d, n)
            if os.path.exists(p):
                return ImageFont.truetype(p, size)
    return ImageFont.load_default(size)


def _wrap(draw, text, font, max_w):
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=font) <= max_w:
            line = trial
        else:
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def _gradient(color_top, color_bottom):
    img = Image.new("RGB", (W, H), color_top)
    px = img.load()
    for y in range(H):
        t = y / (H - 1)
        row = tuple(int(color_top[i] + (color_bottom[i] - color_top[i]) * t) for i in range(3))
        for x in range(W):
            px[x, y] = row
    return img


def _photo_background(photo):
    """Cover-crop the photo to the card size and darken it so white text stays readable."""
    pw, ph = photo.size
    scale = max(W / pw, H / ph)
    photo = photo.resize((int(pw * scale) + 1, int(ph * scale) + 1), Image.LANCZOS)
    left, top = (photo.width - W) // 2, (photo.height - H) // 2
    base = photo.crop((left, top, left + W, top + H)).convert("RGBA")
    shade = Image.new("RGBA", (W, H))
    px = shade.load()
    for y in range(H):
        alpha = int(255 * (0.55 + 0.38 * (y / (H - 1))))  # darker towards the text at the bottom
        for x in range(W):
            px[x, y] = (8, 14, 28, alpha)
    return Image.alpha_composite(base, shade).convert("RGB")


def render_card(headline: str, category: str, source: str, state: str = "National",
                developing: bool = False, spotlight: bool = False, when: datetime = None,
                photo=None, photo_credit: str = "") -> bytes:
    when = (when or datetime.now(timezone.utc)).astimezone(IST)
    img = _photo_background(photo) if photo is not None else _gradient((13, 22, 41), (24, 40, 72))
    d = ImageDraw.Draw(img)
    pad = 80
    accent = CATEGORY_COLORS.get(category, "#3A86FF")

    # Brand bar
    d.rectangle([0, 0, W, 14], fill=accent)
    d.text((pad, 70), config.BRAND_NAME.upper(), font=_font(38), fill="#FFFFFF")
    d.text((W - pad, 78), when.strftime("%d %b %Y").upper(), font=_font(30, False),
           fill="#9FB3D1", anchor="ra")

    # Category + region pills
    y, x = 190, pad
    for label, color, txt in [(category.upper(), accent, "#0D1629"),
                              (state.upper() if state != "National" else "INDIA", "#2B3A5C", "#E8EEF9")]:
        f = _font(30)
        tw = d.textlength(label, font=f)
        d.rounded_rectangle([x, y, x + tw + 44, y + 62], radius=31, fill=color)
        d.text((x + 22, y + 11), label, font=f, fill=txt)
        x += tw + 44 + 18

    if spotlight:
        d.text((pad, y + 90), "SPOTLIGHT · LESS-COVERED STORY", font=_font(28), fill="#2EC4B6")
    elif developing:
        d.text((pad, y + 90), "DEVELOPING STORY", font=_font(28), fill="#FFB703")

    # Headline: shrink until it fits
    max_w, top, bottom = W - 2 * pad, 360, 1060
    size = 84
    while size >= 44:
        f = _font(size)
        lines = _wrap(d, headline, f, max_w)
        if len(lines) * int(size * 1.22) <= bottom - top:
            break
        size -= 4
    yy = top
    for line in lines:
        d.text((pad, yy), line, font=f, fill="#FFFFFF")
        yy += int(size * 1.22)

    # Footer
    d.line([pad, 1120, W - pad, 1120], fill="#2B3A5C", width=3)
    # The image carries only the headline and the brand. Source link, photo credit and the
    # representative-image note live in the caption (see pipeline.build_texts).
    d.text((pad, 1200), config.BRAND_HANDLE, font=_font(38), fill=accent)

    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88, optimize=True)
    return buf.getvalue()


def _base(accent: str):
    img = _gradient((13, 22, 41), (24, 40, 72))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 14], fill=accent)
    return img, d


def _jpeg(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88, optimize=True)
    return buf.getvalue()


def render_digest_cover(count: int, when: datetime = None) -> bytes:
    when = (when or datetime.now(timezone.utc)).astimezone(IST)
    accent = "#FFB703"
    img, d = _base(accent)
    pad = 80
    d.text((pad, 70), config.BRAND_NAME.upper(), font=_font(38), fill="#FFFFFF")
    d.text((pad, 420), "TODAY", font=_font(150), fill="#FFFFFF")
    d.text((pad, 590), "IN SHORT", font=_font(150), fill=accent)
    d.text((pad, 800), f"{count} stories that mattered across India", font=_font(44, False), fill="#E8EEF9")
    d.text((pad, 880), when.strftime("%A, %d %B %Y"), font=_font(36, False), fill="#9FB3D1")
    d.text((pad, 1180), "Swipe  >", font=_font(44), fill=accent)
    d.text((pad, 1275), config.BRAND_HANDLE, font=_font(30), fill=accent)
    d.text((W - pad, 1277), "AI-assisted summary", font=_font(26, False), fill="#7F93B5", anchor="ra")
    return _jpeg(img)


def render_digest_slide(index: int, total: int, headline: str, category: str, source: str) -> bytes:
    accent = CATEGORY_COLORS.get(category, "#3A86FF")
    img, d = _base(accent)
    pad = 80
    d.text((pad, 70), config.BRAND_NAME.upper(), font=_font(38), fill="#FFFFFF")
    d.text((W - pad, 78), f"{index} / {total}", font=_font(34, False), fill="#9FB3D1", anchor="ra")
    f = _font(30)
    label = category.upper()
    tw = d.textlength(label, font=f)
    d.rounded_rectangle([pad, 190, pad + tw + 44, 252], radius=31, fill=accent)
    d.text((pad + 22, 201), label, font=f, fill="#0D1629")
    max_w, top, bottom = W - 2 * pad, 330, 1060
    size = 80
    while size >= 44:
        fh = _font(size)
        lines = _wrap(d, headline, fh, max_w)
        if len(lines) * int(size * 1.22) <= bottom - top:
            break
        size -= 4
    y = top
    for line in lines:
        d.text((pad, y), line, font=fh, fill="#FFFFFF")
        y += int(size * 1.22)
    d.line([pad, 1120, W - pad, 1120], fill="#2B3A5C", width=3)
    d.text((pad, 1150), f"Source: {source}", font=_font(36), fill="#E8EEF9")
    d.text((pad, 1275), config.BRAND_HANDLE, font=_font(30), fill=accent)
    return _jpeg(img)
