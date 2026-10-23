from kol_matcher.schemas.creator import Creator
from kol_matcher.schemas.scoring import Flag, FlagType

# Phrases, not single words: creators often write "ติดต่องานแบรนด์" (brand-deal contact) or
# "รีวิวร้าน" (restaurant reviews), so bare "แบรนด์" / "ร้าน" would flag real creators.
SHOP_PHRASES = [
    "ร้านของเรา", "อุดหนุนร้าน", "สินค้าตรงปก", "ส่งของทุกวัน", "ไม่รับเคลม",
    "ราคาส่ง", "รับตัวแทน", "เปิดบิล", "official shop", "official store",
]
# "ไม่รับงาน..." usually names one kind of job the creator refuses ("ไม่รับรีวิวสกินแคร์" =
# no skincare reviews), not a refusal of all work. So the flag quotes the line for a human to judge.
RESTRICTION_PHRASES = ["ไม่รับงาน", "ไม่รับรีวิว"]


def account_flags(creator: Creator) -> list[Flag]:
    """Bio-based flags for human review. Heuristic, so flags only: never a filter or a score."""
    bio = creator.bio.lower()
    flags = []
    if hit := _first_match(bio, SHOP_PHRASES):
        flags.append(Flag(type=FlagType.shop_account, detail=f"bio contains '{hit}'"))
    if hit := _first_match(bio, RESTRICTION_PHRASES):
        line = next(line.strip() for line in creator.bio.splitlines() if hit in line.lower())
        flags.append(Flag(type=FlagType.work_restriction, detail=f"bio: '{line}'"))
    return flags


def _first_match(text: str, phrases: list[str]) -> str | None:
    return next((p for p in phrases if p in text), None)
