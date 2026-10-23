import pytest

from kol_matcher.schemas.creator import Tier, tier_for


@pytest.mark.parametrize(
    ("followers", "tier"),
    [
        (0, Tier.nano),
        (9_999, Tier.nano),
        (10_000, Tier.micro),
        (99_999, Tier.micro),
        (100_000, Tier.mid),
        (499_999, Tier.mid),
        (500_000, Tier.macro),
        (999_999, Tier.macro),
        (1_000_000, Tier.mega),
    ],
)
def test_tier_boundaries(followers, tier):
    assert tier_for(followers) == tier
