"""Digital business card: a public /card page, a QR code that always points at the live site, and a vCard."""
from __future__ import annotations

import io
import os

import qrcode
from flask import current_app, request
from qrcode.constants import ERROR_CORRECT_H
from qrcode.image.svg import SvgPathImage


def public_base() -> str:
    """The address people should use: the deployed domain, else today's public link, else this computer."""
    cfg = current_app.config
    return (cfg.get("PUBLIC_SITE_URL") or cfg.get("TUNNEL_URL") or os.environ.get("TUNNEL_URL")
            or request.host_url).rstrip("/")


def is_local(url: str) -> bool:
    return any(h in url for h in ("127.0.0.1", "localhost", "0.0.0.0", "192.168.", "10.0."))


def qr_svg(data: str) -> str:
    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_H, border=1, image_factory=SvgPathImage, box_size=10)
    qr.add_data(data)
    img = qr.make_image()
    svg = img.to_string(encoding="unicode")
    return svg.replace("<svg ", '<svg role="img" aria-label="QR code" class="qr-svg" ', 1)


def qr_png(data: str, size: int = 900) -> bytes:
    """High-resolution PNG with the logo in the middle (error correction H keeps it scannable)."""
    from PIL import Image
    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_H, border=2, box_size=20)
    qr.add_data(data)
    img = qr.make_image(fill_color="#0b3442", back_color="white").convert("RGBA")
    img = img.resize((size, size), Image.NEAREST)
    logo_path = os.path.join(current_app.static_folder, "img", "logo-small.png")
    if os.path.exists(logo_path):
        logo = Image.open(logo_path).convert("RGBA")
        side = size // 4
        logo.thumbnail((side, side), Image.LANCZOS)
        pad = Image.new("RGBA", (side + 24, side + 24), (255, 255, 255, 255))
        pad.alpha_composite(logo, ((pad.width - logo.width) // 2, (pad.height - logo.height) // 2))
        img.alpha_composite(pad, ((size - pad.width) // 2, (size - pad.height) // 2))
    out = io.BytesIO()
    img.convert("RGB").save(out, "PNG", optimize=True)
    return out.getvalue()


def vcard(site: dict, url: str) -> str:
    def esc(v: str) -> str:
        return (v or "").replace("\\", "\\\\").replace(",", "\\,").replace(";", "\;").replace("\n", "\\n")
    lines = ["BEGIN:VCARD", "VERSION:3.0", f"FN:{esc(site.get('business_name'))}", f"ORG:{esc(site.get('business_name'))}",
             f"NOTE:{esc(site.get('tagline'))}", f"URL:{url}"]
    if site.get("contact_phone"):
        lines.append(f"TEL;TYPE=WORK,VOICE:{site['contact_phone']}")
    if site.get("whatsapp_number"):
        lines.append(f"TEL;TYPE=CELL:+{site['whatsapp_number'].lstrip('+')}")
    if site.get("contact_email"):
        lines.append(f"EMAIL;TYPE=WORK:{site['contact_email']}")
    if site.get("address"):
        lines.append(f"ADR;TYPE=WORK:;;{esc(site['address'])};;;;")
    lines.append("END:VCARD")
    return "\r\n".join(lines) + "\r\n"
