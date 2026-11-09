"""Real photographs for the 'Meet our horses' slider.

On start-up (in the background) the site downloads one real, freely licensed photo per horse from Wikimedia
Commons — the photo library behind Wikipedia. For each horse it tries the Wikipedia articles for its breed, then its
coat colour, and uses the article's lead photograph. Every photo's photographer and licence are saved and shown on the
card, as the licences (Creative Commons / public domain) require.

These are photos of the same *breed and colour*, not of your own horses. Upload real photos of your horses in
Admin → Horses — an uploaded photo always takes priority.

Run by hand:  flask --app app fetch-horse-photos      (add --refresh to download again)
"""
from __future__ import annotations

import io
import json
import os
import re
import threading
import time
from urllib.parse import quote, unquote

import requests

UA = {"User-Agent": "SeahorseRideWebsite/1.0 (horse-profile photo setup; contact via site owner)"}

# Wikipedia articles to try for each starter horse, best match first.
CANDIDATES: dict[str, list[str]] = {
    "Peanut": ["Shetland_pony", "Pony"],
    "Biscuit": ["Welsh_Pony_and_Cob", "Welsh_Mountain_Pony", "Dun_gene"],
    "Pebbles": ["Highland_pony", "New_Forest_pony", "Exmoor_pony"],
    "Coco": ["Basotho_pony", "Dartmoor_pony", "Fell_pony"],
    "Willow": ["Arabian_horse", "Gray_horse"],
    "Sage": ["Boerperd", "Bay_(horse)", "Kabardin_horse"],
    "Honey": ["Palomino_horse", "American_Quarter_Horse"],
    "Storm": ["Bay_(horse)", "Irish_Draught", "Thoroughbred"],
    "Coral": ["Chestnut_(horse_color)", "Haflinger", "Suffolk_Punch"],
    "Sandy": ["Appaloosa", "Knabstrupper"],
    "Misty": ["Connemara_pony", "Gray_horse", "Andalusian_horse"],
    "Shadow": ["Friesian_horse", "Black_(horse)"],
    "Breeze": ["Arabian_horse", "Akhal-Teke"],
    "Blaze": ["Thoroughbred", "Blaze_(horse_marking)", "Hanoverian_horse"],
    "Tide": ["Dutch_Warmblood", "Hanoverian_horse", "Holsteiner_horse"],
    "Atlas": ["Shire_horse", "Percheron", "Gypsy_horse"],
    "Duke": ["Clydesdale_horse", "Roan_(horse)", "Belgian_horse"],
}


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _lead_image(title: str) -> str | None:
    try:
        r = requests.get(f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote(title)}", headers=UA, timeout=15)
        if r.status_code != 200:
            return None
        data = r.json()
        src = (data.get("originalimage") or data.get("thumbnail") or {}).get("source")
        if src and re.search(r"\.(jpe?g|png|webp)$", src, re.I):
            return src
    except (requests.RequestException, ValueError):
        return None
    return None


def _credit(image_url: str) -> dict:
    """Photographer + licence from Wikimedia's file description."""
    filename = unquote(image_url.rsplit("/", 1)[-1])
    filename = re.sub(r"^\d+px-", "", filename)
    try:
        r = requests.get("https://en.wikipedia.org/w/api.php", headers=UA, timeout=15, params={
            "action": "query", "titles": f"File:{filename}", "prop": "imageinfo", "iiprop": "url|extmetadata",
            "iiurlwidth": 900, "format": "json"})
        page = next(iter(r.json()["query"]["pages"].values()))
        info = page["imageinfo"][0]
        meta = info.get("extmetadata", {})
        artist = re.sub(r"<[^>]+>", "", meta.get("Artist", {}).get("value", "")).strip() or "Unknown photographer"
        return {"artist": artist[:80], "license": meta.get("LicenseShortName", {}).get("value", "See source"),
                "source": info.get("descriptionurl", f"https://commons.wikimedia.org/wiki/File:{quote(filename)}"),
                "download": info.get("thumburl") or info.get("url") or image_url}
    except (requests.RequestException, ValueError, KeyError, StopIteration, IndexError):
        return {"artist": "Wikimedia Commons", "license": "See source",
                "source": f"https://commons.wikimedia.org/wiki/File:{quote(filename)}", "download": image_url}


def fetch_horse_photos(folder: str, names: list[str], log=print, refresh: bool = False) -> dict:
    from PIL import Image, ImageOps

    os.makedirs(folder, exist_ok=True)
    credits_path = os.path.join(folder, "credits.json")
    try:
        credits = json.load(open(credits_path))
    except (OSError, ValueError):
        credits = {}
    used = {c.get("source") for c in credits.values()}
    got, failed, misses = [], [], 0
    for name in names:
        target = os.path.join(folder, f"{slug(name)}.jpg")
        if os.path.exists(target) and not refresh:
            continue
        saved = False
        for title in CANDIDATES.get(name, []):
            src = _lead_image(title)
            if not src:
                continue
            credit = _credit(src)
            if credit["source"] in used:  # never give two horses the same photo
                continue
            try:
                r = requests.get(credit["download"], headers=UA, timeout=30)
                r.raise_for_status()
                with Image.open(io.BytesIO(r.content)) as im:
                    im = ImageOps.exif_transpose(im).convert("RGB")
                    im.thumbnail((900, 900))
                    im.save(target, "JPEG", quality=82, optimize=True, progressive=True)
            except Exception:  # noqa: BLE001
                continue
            credit["article"] = title
            credits[name] = credit
            used.add(credit["source"])
            got.append(name)
            saved = True
            log(f"Horse photo: {name} ← {title.replace('_', ' ')} ({credit['license']})")
            break
        if not saved:
            failed.append(name)
            misses += 1
            if misses >= 3 and not got:
                log("Horse photos: Wikipedia isn't reachable right now — showing 'Photo coming soon' instead.")
                break
    with open(credits_path, "w") as fh:
        json.dump(credits, fh, indent=1)
    with open(os.path.join(folder, "_status.json"), "w") as fh:
        json.dump({"at": time.time(), "downloaded": got, "failed": failed}, fh)
    return {"downloaded": got, "failed": failed}


def fetch_in_background(folder: str, names: list[str], logger) -> None:
    missing = [n for n in names if n in CANDIDATES and not os.path.exists(os.path.join(folder, f"{slug(n)}.jpg"))]
    if not missing:
        return
    try:
        if time.time() - json.load(open(os.path.join(folder, "_status.json")))["at"] < 6 * 3600:
            return
    except (OSError, ValueError, KeyError):
        pass
    threading.Thread(target=fetch_horse_photos, args=(folder, missing, logger.info), daemon=True).start()


class HorsePhotoLibrary:
    def __init__(self, folder: str):
        self.folder = folder
        self._credits: tuple[float, dict] | None = None

    def file_for(self, name: str) -> str | None:
        f = f"{slug(name)}.jpg"
        return f if os.path.exists(os.path.join(self.folder, f)) else None

    def credit(self, name: str) -> dict | None:
        if not self._credits or time.time() - self._credits[0] > 30:
            try:
                data = json.load(open(os.path.join(self.folder, "credits.json")))
            except (OSError, ValueError):
                data = {}
            self._credits = (time.time(), data)
        return self._credits[1].get(name)
