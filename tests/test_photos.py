import io

from PIL import Image

from seahorse import photos


def _jpeg(w=3000, h=2000):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (30, 90, 110)).save(buf, "JPEG")
    return buf.getvalue()


def test_fetch_resizes_and_library_picks_roles(tmp_path, monkeypatch):
    monkeypatch.setattr(photos, "_download", lambda url: _jpeg() if "unsplash" in url else None)
    result = photos.fetch_photos(str(tmp_path), log=lambda *_: None)
    assert "beach-group" in result["downloaded"] and "herd" in result["failed"]
    with Image.open(tmp_path / "beach-group.jpg") as im:
        assert max(im.size) == 2000
    lib = photos.PhotoLibrary(str(tmp_path))
    assert lib.for_role("hero") == "beach-group.jpg"
    assert lib.for_package("couples", 0) == "couple.jpg"
    assert lib.for_package("sunset-ride", 0) == "beach-shore.jpg"  # falls back down the preference list


def test_offline_gives_up_quickly(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(photos, "_download", lambda url: calls.append(url))
    photos.fetch_photos(str(tmp_path), log=lambda *_: None)
    assert len(calls) <= 4
    assert photos.PhotoLibrary(str(tmp_path)).for_role("hero") is None


def test_horse_photos_unique_and_credited(tmp_path, monkeypatch):
    from seahorse import horse_photos as hp
    monkeypatch.setattr(hp, "_lead_image", lambda title: f"https://upload.wikimedia.org/x/{title}.jpg")
    calls = {}

    def credit(url):
        name = url.rsplit("/", 1)[-1]
        # two different articles that point at the same photo must not be reused
        src = "same-photo" if name in ("Bay_(horse).jpg", "Thoroughbred.jpg") else name
        return {"artist": "A. Photographer", "license": "CC BY-SA 4.0", "source": src, "download": url}
    monkeypatch.setattr(hp, "_credit", credit)

    class R:
        def __init__(self): self.content = _jpeg(1200, 900)
        def raise_for_status(self): pass
    monkeypatch.setattr(hp.requests, "get", lambda *a, **k: calls.setdefault(a[0], R()))
    out = hp.fetch_horse_photos(str(tmp_path), ["Storm", "Blaze"], log=lambda *_: None)
    assert out["downloaded"] == ["Storm", "Blaze"]
    import json
    cr = json.load(open(tmp_path / "credits.json"))
    assert cr["Storm"]["source"] != cr["Blaze"]["source"] and cr["Storm"]["license"] == "CC BY-SA 4.0"
    lib = hp.HorsePhotoLibrary(str(tmp_path))
    assert lib.file_for("Storm") == "storm.jpg" and lib.credit("Blaze")["artist"] == "A. Photographer"


def test_own_horse_photo_overrides_stock(app, tmp_path):
    import os
    from seahorse.models import Horse
    own = os.path.join(app.static_folder, "img", "horses", "own")
    path = os.path.join(own, "willow.png")
    open(path, "wb").write(b"\x89PNG\r\n\x1a\n")
    try:
        with app.test_request_context("/"):
            h = Horse.query.filter_by(name="Willow").first() or Horse(name="Willow")
            assert app.jinja_env.globals["horse_photo"](h).endswith("/static/img/horses/own/willow.png")
            assert app.jinja_env.globals["horse_credit"](h) is None
    finally:
        os.remove(path)
