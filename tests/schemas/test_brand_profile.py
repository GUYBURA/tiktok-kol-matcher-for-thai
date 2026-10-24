import pytest
from pydantic import ValidationError

from kol_matcher.schemas.brand import BrandProfile, profile_hash

BASE = {
    "brand_name": "Example Running Co.", "category": "sportswear", "products": ["running shoes"],
    "price_tier": "mid", "target_audience": "Thai adults 25-40", "tone": "encouraging",
    "content_themes": ["beginner running training"], "keywords_th": ["วิ่งมือใหม่"],
    "keywords_en": ["running shoes"], "hashtags": ["#วิ่ง"],
}


def test_profile_hash_ignores_key_order_and_input_form():
    reordered = dict(reversed(list(BASE.items())))
    assert profile_hash(BrandProfile(**BASE)) == profile_hash(BrandProfile(**reordered))
    assert profile_hash(BrandProfile(**BASE)) == profile_hash(
        BrandProfile.model_validate_json(BrandProfile(**BASE).model_dump_json()))


@pytest.mark.parametrize("field, value", [
    ("tone", "playful"), ("content_themes", ["trail running"]), ("competitors", ["Other Co."]),
    ("keywords_th", ["วิ่งมือใหม่", "รองเท้าวิ่ง"]), ("target_tiers", ["micro"]),
])
def test_profile_hash_changes_with_any_field(field, value):
    assert profile_hash(BrandProfile(**BASE)) != profile_hash(BrandProfile(**{**BASE, field: value}))


def test_profile_hash_is_short_and_stable():
    h = profile_hash(BrandProfile(**BASE))
    assert len(h) == 16 and h == profile_hash(BrandProfile(**BASE))


@pytest.mark.parametrize("bad", [
    {"hashtags": ["วิ่ง"]},                                   # missing '#'
    {"content_themes": ["a", "b", "c", "d", "e", "f"]},       # max 5
    {"price_tier": "premium"},                                 # not in the enum
    {"unknown_field": "x"},                                    # extra fields are rejected
    {"products": []},                                          # at least one
])
def test_invalid_profiles_rejected(bad):
    with pytest.raises(ValidationError):
        BrandProfile(**{**BASE, **bad})
