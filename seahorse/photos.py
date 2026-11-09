"""Horse photography for the website.

On first start the site downloads its photos into ``static/img/photos/`` (in a background thread, so the
server starts immediately). Sources, in order of preference:

1. The four horse photos from the original Seahorse Lovable project (horse-herd, horse-1996333, ...).
2. Free photos from Unsplash (Unsplash License — free for commercial use, credit appreciated).

Every image is checked with Pillow, resized to a web-friendly size and saved as an optimised JPEG.
Run it by hand with:   flask --app app fetch-photos
You can also simply drop your own JPGs into static/img/photos/ — any file name works — or upload photos
in Admin → Gallery / Packages / Settings (hero photo). Your own uploads always take priority.
"""
from __future__ import annotations

import io
import json
import os
import threading
import time

import requests

LOVABLE_PROJECT = "5daab56e-f082-4c3a-91ac-71ea23229a10"
LOVABLE_HOSTS = [f"id-preview--{LOVABLE_PROJECT}.lovable.app", f"{LOVABLE_PROJECT}.lovableproject.com"]


def _lovable(asset_id: str, name: str) -> list[str]:
    return [f"https://{h}/__l5e/assets-v1/{asset_id}/{name}" for h in LOVABLE_HOSTS]


def _unsplash(photo_id: str) -> list[str]:
    return [f"https://unsplash.com/photos/{photo_id}/download?force=true"]


# key -> (candidate URLs, alt text, credit)
SOURCES: dict[str, tuple[list[str], str, str]] = {
    "beach-group": (_unsplash("rtNOCN8mMC8"), "A group of riders on horseback along the beach", "Unsplash"),
    "beach-rider": (_unsplash("1FPlFiUC_s0"), "A rider on horseback at the water's edge", "Unsplash"),
    "beach-pair": (_unsplash("WYxZ8yHXlmk"), "Two riders on horseback on the beach", "Unsplash"),
    "beach-shore": (_unsplash("75HO6lhwuk4"), "A horse and rider on the shoreline", "Unsplash"),
    "couple": (_unsplash("Jc2EcxM_sy0"), "A couple riding horses together", "Unsplash"),
    "sea-gallop": (_unsplash("eT19cqwIXaY"), "White horses galloping through shallow sea water", "Unsplash"),
    "brown-horse": (_unsplash("fN2bI6GQ0J0"), "A rider on a brown horse", "Unsplash"),
    "open-ride": (_unsplash("WM98jaiBY78"), "A rider crossing open ground under a big sky", "Unsplash"),
    "herd": (_lovable("67a7f99b-ca68-458a-a373-19d969e13a6f", "horse-herd.jpeg"),
             "A herd of horses moving through a misty field at first light", "Seahorse"),
    "white-horse": (_lovable("fbc4c50f-25f6-4f74-b342-256c025ff35f", "horse-1996333.jpeg"),
                    "A white horse in a leafy setting", "Seahorse"),
    "horse-portrait": (_lovable("75fd982f-9f35-432e-8185-c3fce6fe1c67", "horse-1123250.jpeg"), "A horse portrait", "Seahorse"),
    "horse-field": (_lovable("e404143e-0b02-47e3-8e7e-3bb165f4e7df", "horse-1510543.jpeg"), "A horse in a field", "Seahorse"),
}

# Photos supplied by the business, shipped with the project (static/img/photos/). They are never overwritten
# by downloads because files that already exist are skipped.
LOCAL = {
    "beach-group": "Riders splashing through the surf on a guided beach ride",
    "beach-sunset": "Riders on the shoreline at golden hour, reflected in the wet sand",
    "white-horse": "A smiling rider on a white horse on a winding hillside path",
    "mountain-ride": "A rider on a brown horse above the clouds",
}

# Which photos suit which part of the site (first available wins).
ROLES = {
    "hero": ["beach-group", "beach-sunset", "beach-pair", "sea-gallop", "herd"],
    "story": ["white-horse", "beach-rider", "couple", "horse-portrait"],
    "events": ["beach-sunset", "beach-pair", "couple", "beach-group"],
    "auth": ["beach-sunset", "white-horse", "sea-gallop", "herd"],
    "auth-alt": ["white-horse", "mountain-ride", "beach-sunset"],
    "band": ["beach-sunset", "sea-gallop", "herd", "beach-shore"],
}
# Preferred photos for seeded packages by slug (first available wins).
PACKAGE_PHOTOS = {
    "beach-ride": ["beach-group", "beach-rider"], "sunset-ride": ["beach-sunset", "beach-shore"],
    "couples": ["couple", "beach-sunset"], "small-group": ["beach-group", "beach-pair"],
    "family-fun": ["white-horse", "brown-horse"], "friends-celebration": ["beach-pair", "beach-sunset"],
    "tourist": ["mountain-ride", "open-ride"], "birthday-party": ["sea-gallop", "beach-group"],
    "corporate": ["herd", "mountain-ride"], "custom": ["mountain-ride", "white-horse"],
}


class PhotoLibrary:
    def __init__(self, folder: str):
        self.folder = folder
        self._cache: tuple[float, list[str]] | None = None

    def files(self) -> list[str]:
        if self._cache and time.time() - self._cache[0] < 10:
            return self._cache[1]
        try:
            names = sorted(f for f in os.listdir(self.folder) if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp")))
        except OSError:
            names = []
        self._cache = (time.time(), names)
        return names

    def _find(self, key: str) -> str | None:
        for f in self.files():
            if os.path.splitext(f)[0] == key:
                return f
        return None

    def find(self, key: str) -> str | None:
        return self._find(key)

    def for_role(self, role: str) -> str | None:
        for key in ROLES.get(role, []):
            if (f := self._find(key)):
                return f
        files = self.files()
        return files[0] if files else None

    def for_index(self, i: int) -> str | None:
        files = self.files()
        return files[i % len(files)] if files else None

    def for_package(self, slug: str, i: int) -> str | None:
        for key in PACKAGE_PHOTOS.get(slug, []):
            if (f := self._find(key)):
                return f
        return self.for_index(i)

    def alt(self, filename: str | None) -> str:
        if not filename:
            return ""
        key = os.path.splitext(filename)[0]
        return LOCAL.get(key) or SOURCES.get(key, ([], "Horse riding with Seahorse Ride", ""))[1]


def _download(url: str) -> bytes | None:
    try:
        r = requests.get(url, timeout=25, allow_redirects=True,
                         headers={"User-Agent": "Mozilla/5.0 (SeahorseRide photo setup)"})
        if r.status_code == 200 and r.headers.get("Content-Type", "").startswith("image"):
            return r.content
    except requests.RequestException:
        return None
    return None


def fetch_photos(folder: str, log=print, only_missing: bool = True) -> dict:
    from PIL import Image, ImageOps

    os.makedirs(folder, exist_ok=True)
    got, failed, offline_strikes = [], [], 0
    for key, (urls, _alt, credit) in SOURCES.items():
        target = os.path.join(folder, f"{key}.jpg")
        if only_missing and os.path.exists(target):
            continue
        data = None
        for url in urls:
            data = _download(url)
            if data:
                break
        if not data:
            failed.append(key)
            offline_strikes += 1
            if offline_strikes >= 4 and not got:
                log("Photo download: no internet access to the photo sites — skipping (the site uses illustrations).")
                break
            continue
        try:
            with Image.open(io.BytesIO(data)) as im:
                im = ImageOps.exif_transpose(im).convert("RGB")
                im.thumbnail((2000, 2000))
                im.save(target, "JPEG", quality=80, optimize=True, progressive=True)
            got.append(key)
            log(f"Photo downloaded: {key} ({credit})")
        except Exception:  # noqa: BLE001 — not a valid image
            failed.append(key)
    with open(os.path.join(folder, "_status.json"), "w") as fh:
        json.dump({"downloaded": got, "failed": failed, "at": time.time()}, fh)
    return {"downloaded": got, "failed": failed}


def fetch_in_background(folder: str, logger) -> None:
    """Start a one-off background download of any missing photos (skipped if tried in the last 6 hours)."""
    status = os.path.join(folder, "_status.json")
    missing = [k for k in SOURCES if not os.path.exists(os.path.join(folder, f"{k}.jpg"))]
    if not missing:
        return
    try:
        if time.time() - json.load(open(status))["at"] < 6 * 3600:
            return
    except (OSError, ValueError, KeyError):
        pass
    threading.Thread(target=fetch_photos, args=(folder, logger.info), daemon=True).start()
