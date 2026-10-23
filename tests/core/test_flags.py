from datetime import datetime

import pytest

from kol_matcher.core.flags import account_flags
from kol_matcher.schemas.creator import Creator, Tier
from kol_matcher.schemas.scoring import FlagType


def creator(bio):
    return Creator(handle="x", bio=bio, followers=20_000, tier=Tier.micro,
                   profile_url="https://www.tiktok.com/@x", scraped_at=datetime.now())


def flag_types(bio):
    return [f.type for f in account_flags(creator(bio))]


# Bios from the live Yuedpao discovery run.
@pytest.mark.parametrize("bio", [
    "ขอบคุณที่อุดหนุนร้านของเราน่าาาา🧺",
    "สินค้าตรงปก ส่งของทุกวันจ้าา\nมายู ส",
    "📌1-3ดาว⭐️ไม่รับเคลม❌\nสินค้ามีปัญหาท",
    "Official Shop | ส่งไว",
])
def test_shop_accounts_flagged(bio):
    assert flag_types(bio) == [FlagType.shop_account]


@pytest.mark.parametrize("bio", [
    "รับรีวิวเสื้อผ้า ติดต่อ IG : taipal",
    "แต่งตัว | แต่งหน้า | Lifestyle",
    "ติดต่องานแบรนด์ Line: @abc",          # brand-deal contact, not a shop
    "รีวิวร้านอาหารอร่อยทั่วกรุงเทพ",         # restaurant reviewer, not a shop
    "PREVIEW ATIPA\nAffiliate\nตะกร้าสินค้า",  # affiliate creator: kept, not a shop
    "",
])
def test_creators_not_flagged(bio):
    assert flag_types(bio) == []


# Real bios: each refuses one kind of job, not all work, so the flag must quote the restriction.
@pytest.mark.parametrize(("bio", "quoted"), [
    ("อุดหนุนสินค้าด้านล่างนะคะ👇\nรับรีวิวค้าบ🥹   ไม่รับรีวิวสกินแคร์\nช่องสำรอง", "ไม่รับรีวิวสกินแคร์"),
    ("🩵 : Nattharika Sangketkit \nไม่รับงานปิดจุก ทุกแบบ", "ไม่รับงานปิดจุก ทุกแบบ"),
])
def test_work_restriction_quotes_the_line(bio, quoted):
    [flag] = account_flags(creator(bio))
    assert flag.type == FlagType.work_restriction
    assert quoted in flag.detail
    assert "ช่องสำรอง" not in flag.detail  # only the matching line


def test_both_flags():
    assert flag_types("ร้านของเรา\nไม่รับงานต่างจังหวัด") == [FlagType.shop_account, FlagType.work_restriction]


# --- shop handle, expertise ------------------------------------------------------------------

def creator_h(handle, bio=""):
    return Creator(handle=handle, bio=bio, followers=20_000, tier=Tier.micro,
                   profile_url="u", scraped_at=datetime.now())


@pytest.mark.parametrize("handle", ["stylepai.shop", "mint_store", "yuedpao-official"])
def test_shop_handle_flagged(handle):
    [flag] = account_flags(creator_h(handle))
    assert flag.type == FlagType.shop_account and "handle" in flag.detail


@pytest.mark.parametrize("handle", ["shoppingwithmint", "chuuchop", "bunga_ariya"])
def test_handle_without_shop_part_not_flagged(handle):
    assert account_flags(creator_h(handle)) == []


@pytest.mark.parametrize(("bio", "title"), [
    ("พญ.มินท์ แพทย์ผิวหนัง", "พญ."),
    ("ภก. เภสัชกรใจดี", "ภก."),
    ("Page Facebook โค้ชก้อง : Fit & Run", "โค้ช"),
    ("Dr. Bank | skincare science", "dr."),
])
def test_self_declared_expertise_unverified(bio, title):
    flags = [f for f in account_flags(creator(bio)) if f.type == FlagType.self_declared_expertise]
    assert len(flags) == 1
    assert title in flags[0].detail and "unverified" in flags[0].detail


def test_fortune_teller_is_not_expertise():
    assert flag_types("หมอดูไพ่ยิปซี แม่นมาก") == []


# --- content flags ---------------------------------------------------------------------------

from kol_matcher.core.flags import HIGH_SPONSORED_RATIO, content_flags
from kol_matcher.schemas.brand import BrandProfile
from kol_matcher.schemas.creator import EnrichedCreator, Video

PROFILE = BrandProfile(
    brand_name="Yuedpao", category="clothing", products=["t-shirts"], tone="casual", price_tier="mid",
    target_audience="Thai adults", content_themes=["outfits"], keywords_th=["เสื้อยืด"], keywords_en=["tee"],
    competitors=["Uniqlo", "GU", "ห่านคู่"],
)


def enriched(captions, ads=0, bio=""):
    videos = [Video(id=str(i), url="u", caption=c, created_at=datetime.now(), views=100, likes=1,
                    comments=0, shares=0, is_ad=i < ads) for i, c in enumerate(captions)]
    return EnrichedCreator(creator=creator_h("x", bio), videos=videos)


def content_types(e):
    return [f.type for f in content_flags(e, PROFILE)]


def test_high_sponsored_ratio_threshold():
    assert content_types(enriched(["a"] * 10, ads=8)) == [FlagType.high_sponsored_ratio]
    assert content_types(enriched(["a"] * 10, ads=7)) == []      # 70% is normal for affiliates
    [flag] = content_flags(enriched(["a"] * 10, ads=10), PROFILE)
    assert flag.detail == "10 of 10 recent clips are ads (100%)"
    assert HIGH_SPONSORED_RATIO == 0.8


def test_brand_safety_in_captions_counts_clips():
    [flag] = content_flags(enriched(["ปั่นสล็อตแตกง่าย", "แต่งตัวไปเที่ยว", "บาคาร่าเว็บตรง"]), PROFILE)
    assert flag.type == FlagType.brand_safety
    assert "2 of 3 captions" in flag.detail


def test_brand_safety_in_bio():
    [flag] = content_flags(enriched(["ootd"], bio="ufabet เว็บตรง"), PROFILE)
    assert flag.type == FlagType.brand_safety and "bio" in flag.detail


def test_booking_slots_are_not_gambling():
    assert content_types(enriched(["รับงานรีวิว เหลือ 2 slot เดือนนี้"])) == []


def test_competitor_mentions():
    flags = content_flags(enriched(["ลองเสื้อ UNIQLO ตัวใหม่", "ห่านคู่ใส่สบาย", "uniqlo haul"]), PROFILE)
    assert [(f.type, f.detail) for f in flags] == [
        (FlagType.competitor_mention, "'Uniqlo' in 2 of 3 recent captions"),
        (FlagType.competitor_mention, "'ห่านคู่' in 1 of 3 recent captions"),
    ]


def test_short_latin_competitor_needs_word_boundary():
    assert content_types(enriched(["styling guide for work", "GUESS the outfit"])) == []
    assert content_types(enriched(["GU haul วันนี้"])) == [FlagType.competitor_mention]
