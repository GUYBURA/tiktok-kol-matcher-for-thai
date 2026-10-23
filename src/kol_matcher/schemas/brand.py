from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from kol_matcher.schemas import stable_hash
from kol_matcher.schemas.creator import Tier


class PriceTier(StrEnum):
    cheap = "cheap"
    mid = "mid"
    high = "high"
    luxury = "luxury"

Hashtag = Annotated[str, Field(pattern=r"^#\S+$")]

class BrandProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    
    brand_name: str = Field(min_length=1, description="Brand name as shown")
    category: str = Field(min_length=1, description="Category of Brand")
    products: list[str] = Field(min_length=1, max_length=10, description="Main products or product lines")
    tone: str = Field(description="Brand voice in a few adjectives, e.g. 'playful, casual' or 'clinical, expert'.")
    price_tier: PriceTier = Field(description="Price positioning")
    target_audience: str = Field(description="Who buys the product: age range, life stage, interests.")
    content_themes: list[str] = Field(min_length=1, max_length=5, description="Up to 5 content themes a matching creator should cover")
    keywords_th: list[str] = Field(min_length=1, max_length=5, description="Thai Keyword up to 5")
    keywords_en: list[str] = Field(min_length=1, max_length=5, description="English Keyword up to 5")
    hashtags: list[Hashtag] = Field(default=[], max_length=10, description="Tiktok hashtags including '#'.")
    competitors: list[str] = Field(default=[], description="Competitor brand names, used for conflict flags")
    target_tiers: list[Tier] = Field(default=[], description="Preferred creator tiers; empty means any.")
    
def profile_hash(p: BrandProfile) -> str:
    return stable_hash(p.model_dump(mode="json"))

class BrandData(BaseModel):
    facebook_page: dict | None = None
    facebook_posts: list[dict] = []
    website_text: str | None = None
    errors: dict[str, str] = {}
    profile_schema: dict