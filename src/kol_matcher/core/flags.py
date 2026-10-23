import re

from kol_matcher.schemas.brand import BrandProfile
from kol_matcher.schemas.creator import Creator, EnrichedCreator
from kol_matcher.schemas.scoring import Flag, FlagType

# All flags are for human review only: never a filter or part of the score.

# Phrases, not single words: creators often write "ติดต่องานแบรนด์" (brand-deal contact) or
# "รีวิวร้าน" (restaurant reviews), so bare "แบรนด์" / "ร้าน" would flag real creators.
SHOP_PHRASES = [
    "ร้านของเรา", "อุดหนุนร้าน", "สินค้าตรงปก", "ส่งของทุกวัน", "ไม่รับเคลม",
    "ราคาส่ง", "รับตัวแทน", "เปิดบิล", "official shop", "official store",
]
# Handle parts split on . _ -, so "stylepai.shop" matches but "shoppingwithmint" does not.
SHOP_HANDLE_PARTS = {"shop", "store", "official"}
# "ไม่รับงาน..." usually names one kind of job the creator refuses ("ไม่รับรีวิวสกินแคร์" =
# no skincare reviews), not a refusal of all work. So the flag quotes the line for a human to judge.
RESTRICTION_PHRASES = ["ไม่รับงาน", "ไม่รับรีวิว"]

# Thai TikTok ad share of 40-60% is normal for affiliate creators; flag only near-all-ad feeds.
HIGH_SPONSORED_RATIO = 0.8
# Online gambling is the main brand-safety risk on Thai TikTok (and illegal in Thailand).
BRAND_SAFETY_TERMS = [
    "สล็อต", "บาคาร่า", "คาสิโน", "เว็บพนัน", "พนันออนไลน์", "แทงบอล", "หวยออนไลน์",
    "เว็บตรงไม่ผ่านเอเย่นต์", "ufabet", "baccarat", "casino", "pgslot", "slotxo", "slot online",
]  # not bare "slot": creators write "เหลือ 2 slot" for booking slots
# Self-declared titles. Unverifiable from public data, so always labelled unverified.
# "หมอ" is left out on purpose: "หมอดู" is a fortune teller.
EXPERTISE_TITLES = ["นพ.", "พญ.", "ภก.", "ภญ.", "ทพ.", "ทญ.", "ดร.", "แพทย์", "เภสัชกร",
                    "นักโภชนาการ", "โค้ช", "dr.", "coach"]


def account_flags(creator: Creator) -> list[Flag]:
    """Flags from the profile alone (bio, handle)."""
    bio = creator.bio.lower()
    flags = []
    if hit := _first_match(bio, SHOP_PHRASES):
        flags.append(Flag(type=FlagType.shop_account, detail=f"bio contains '{hit}'"))
    elif parts := SHOP_HANDLE_PARTS & set(re.split(r"[._-]", creator.handle.lower())):
        flags.append(Flag(type=FlagType.shop_account, detail=f"handle contains '{min(parts)}'"))
    if hit := _first_match(bio, RESTRICTION_PHRASES):
        line = next(line.strip() for line in creator.bio.splitlines() if hit in line.lower())
        flags.append(Flag(type=FlagType.work_restriction, detail=f"bio: '{line}'"))
    if hit := _first_match(bio, EXPERTISE_TITLES):
        flags.append(Flag(type=FlagType.self_declared_expertise, detail=f"bio contains '{hit}' (unverified)"))
    return flags


def content_flags(e: EnrichedCreator, profile: BrandProfile) -> list[Flag]:
    """Flags that need the creator's recent clips and the brand profile."""
    videos = e.videos
    captions = [v.caption.lower() for v in videos]
    flags = []

    ads = sum(v.is_ad for v in videos)
    if videos and ads / len(videos) >= HIGH_SPONSORED_RATIO:
        flags.append(Flag(type=FlagType.high_sponsored_ratio,
                          detail=f"{ads} of {len(videos)} recent clips are ads ({ads / len(videos):.0%})"))

    bio = e.creator.bio.lower()
    if hit := _first_match(bio, BRAND_SAFETY_TERMS):
        flags.append(Flag(type=FlagType.brand_safety, detail=f"bio contains '{hit}'"))
    else:
        hits = [t for t in BRAND_SAFETY_TERMS if any(t in c for c in captions)]
        if hits:
            n = sum(any(t in c for t in hits) for c in captions)
            flags.append(Flag(type=FlagType.brand_safety, detail=f"'{hits[0]}' in {n} of {len(videos)} captions"))

    for name in profile.competitors:
        n = sum(_mentions(c, name) for c in captions)
        if n:
            flags.append(Flag(type=FlagType.competitor_mention,
                              detail=f"'{name}' in {n} of {len(videos)} recent captions"))
    return flags


def _mentions(text: str, name: str) -> bool:
    # Latin names need word boundaries ("GU" must not match "guide"); Thai has no spaces between words.
    name = name.strip().lower()
    if not name:
        return False
    if name.isascii():
        return re.search(rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])", text) is not None
    return name in text


def _first_match(text: str, phrases: list[str]) -> str | None:
    return next((p for p in phrases if p in text), None)
