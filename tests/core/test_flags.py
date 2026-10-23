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
