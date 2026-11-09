"""Starter profiles for the 'Meet our horses' gallery.

These are EXAMPLE profiles for the starter herd so the page looks complete from day one. Replace them with your
real horses' details and photos in Admin → Horses — customers will expect to meet the horses they read about.
Only empty fields are filled, so anything you've edited is never overwritten.
"""
from __future__ import annotations

from .extensions import db
from .models import Horse

# name: (breed, colour, sex, birth_year, height_hands, joined_year, favourite_treat, fun_fact, history, photo_key)
PROFILES: dict[str, tuple] = {
    "Peanut": ("Shetland pony", "Chestnut", "gelding", 2012, "10.2", 2018, "Apple slices",
               "Peanut is the smallest member of the herd and the bossiest at feeding time.",
               "Peanut spent his early years at a children's riding school, where he taught hundreds of little riders "
               "their very first trot. He came to us to enjoy a slower pace and now leads our youngest beach riders "
               "with endless patience.", None),
    "Biscuit": ("Welsh pony", "Dun", "mare", 2014, "12.1", 2019, "Ginger biscuits (of course)",
                "She got her name because she can hear a biscuit packet open from across the paddock.",
                "Biscuit was a Pony Club all-rounder before joining Seahorse. She loves children, stands like a statue "
                "for photos, and is always first to the gate when the kids arrive.", None),
    "Pebbles": ("Shetland cross", "Grey", "mare", 2015, "11.0", 2020, "Carrots",
                "Pebbles collects shells — or at least stands on them and refuses to move until you admire them.",
                "Pebbles was rescued as a youngster and lovingly retrained by our team. She's gentle, steady and "
                "especially good with nervous first-time little riders.", None),
    "Coco": ("Basuto pony", "Brown", "mare", 2013, "13.2", 2017, "Peppermints",
             "Coco is a true South African pony — sure-footed on sand, rocks and dunes.",
             "Basuto ponies are famous for being hardy and kind, and Coco is no exception. She was bred in the Free "
             "State and moved to the coast to become our favourite pony for older children.", None),
    "Willow": ("Arabian cross", "Grey", "mare", 2009, "14.3", 2016, "Watermelon rind",
               "Willow has walked the shoreline more times than anyone can count — nothing on the beach surprises her.",
               "Willow is our most trusted horse for nervous riders. She was an endurance horse in her younger days "
               "and now prefers calm walks along the water's edge, where she looks after her riders like family.",
               "white-horse"),
    "Sage": ("Boerperd", "Bay", "gelding", 2010, "15.0", 2017, "Hay cubes",
             "Sage gives a gentle nose-nudge to every rider before they mount — guides call it his safety check.",
             "The Boerperd is South Africa's own breed, known for good sense and stamina. Sage worked on a farm in "
             "the Karoo before retiring to the coast, and he's unflappable around waves, dogs and kites.", None),
    "Honey": ("Palomino Quarter Horse", "Palomino", "mare", 2012, "14.2", 2019, "Honey-oat treats",
              "Honey's golden coat glows at sunset — she's the star of most of our couples' photos.",
              "Honey came from a trail-riding centre in the Midlands. She has a relaxed, comfortable walk and a "
              "soothing nature that settles even the most anxious riders.", None),
    "Storm": ("Thoroughbred cross", "Dark bay", "gelding", 2011, "15.3", 2018, "Apples",
              "Despite his name, Storm is the calmest horse on the beach — he naps standing up between rides.",
              "Storm raced briefly before discovering he much preferred strolling. Retrained for beach work, he's "
              "become our go-to horse for adult beginners.", "brown-horse"),
    "Coral": ("Boerperd", "Chestnut", "mare", 2013, "15.1", 2018, "Carrots",
              "Coral loves splashing through the shallows and will happily paddle on a hot day.",
              "Coral grew up near the sea and has been part of the Seahorse herd for years. She's gentle in a group "
              "and happy to follow the leader, which makes her perfect for families and friends riding together.", None),
    "Sandy": ("Appaloosa cross", "Spotted dun", "gelding", 2014, "14.3", 2020, "Bananas",
              "Every one of Sandy's spots is different — kids love trying to count them.",
              "Sandy joined us from a holiday farm where he carried guests of every age. He's patient, steady and "
              "always keeps a polite distance from the horse in front.", None),
    "Misty": ("Connemara cross", "Grey", "mare", 2012, "14.2", 2019, "Pears",
              "Misty turns almost white in winter and dapples again by summer.",
              "Connemaras are known for their kind temperament, and Misty is a classic example — quiet, willing and "
              "happy to go at whatever pace her rider is comfortable with.", None),
    "Shadow": ("Friesian cross", "Black", "gelding", 2013, "15.2", 2019, "Sugar-free mints",
               "Shadow's long, wavy mane needs more brushing than anyone else's — he loves the attention.",
               "Shadow came to us as a shy youngster and has grown into one of our most dependable beginner horses. "
               "His striking black coat looks spectacular against the white surf.", "horse-field"),
    "Breeze": ("Arabian", "Bay", "mare", 2014, "15.0", 2020, "Dates",
               "Breeze has competed in endurance rides through the mountains — she still loves an adventure.",
               "Breeze is light, quick and responsive, and for experienced riders a canter with her along the "
               "firm sand is unforgettable. Our guides only pair her with confident riders.", "mountain-ride"),
    "Blaze": ("Thoroughbred", "Chestnut", "gelding", 2012, "16.1", 2019, "Polo mints",
              "Blaze gets his name from the bright white stripe running down his face.",
              "An ex-racehorse with plenty of energy, Blaze was retrained by our head guide over two years. He now "
              "channels his enthusiasm into beach canters with experienced riders.", "horse-portrait"),
    "Tide": ("Warmblood", "Bay", "gelding", 2011, "16.2", 2018, "Apples and carrots",
             "Tide was born on the night of a spring tide — hence the name.",
             "Tide was a show jumper before choosing the beach life. Athletic and powerful, he's the horse our "
             "guides ride themselves on their days off.", None),
    "Atlas": ("Shire cross", "Black and white", "gelding", 2010, "17.0", 2017, "Whole carrots, in one bite",
              "Atlas's hooves are the size of dinner plates — people often stop just to take a photo with him.",
              "Our gentle giant, Atlas, worked on a forestry farm before joining us. He's strong enough to carry taller "
              "and heavier riders comfortably, and he has the softest nature in the herd.", None),
    "Duke": ("Clydesdale cross", "Bay roan", "gelding", 2012, "16.3", 2019, "Pumpkin",
             "Duke has fluffy feathered legs that get comically sandy after every ride.",
             "Duke is calm, solid and steady — the horse we choose for riders who want to feel secure on a big, "
             "reliable mount. He loves a good scratch behind the ears after a ride.", None),
}

FIELDS = ("breed", "colour", "sex", "birth_year", "height_hands", "joined_year", "favourite_treat", "fun_fact", "history")


def fill_starter_profiles() -> int:
    """Fill empty profile fields for starter-herd horses (matched by name). Never overwrites your edits."""
    changed = 0
    for horse in Horse.query.all():
        data = PROFILES.get(horse.name)
        if not data:
            continue
        for field, value in zip(FIELDS, data[:9]):
            if getattr(horse, field) in (None, ""):
                setattr(horse, field, value)
                changed += 1
    if changed:
        db.session.commit()
    return changed


def photo_key(name: str) -> str | None:
    data = PROFILES.get(name)
    return data[9] if data else None
