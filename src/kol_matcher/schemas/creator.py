from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class Tier(StrEnum):
    nano = "nano"
    micro = "micro"
    mid = "mid"
    macro = "macro"
    mega = "mega"


def tier_for(followers: int) -> Tier:
    if followers < 10_000:
        return Tier.nano
    if followers < 100_000:
        return Tier.micro
    if followers < 500_000:
        return Tier.mid
    if followers < 1_000_000:
        return Tier.macro
    return Tier.mega


class Video(BaseModel):
    id: str
    url: str
    caption: str = ""
    created_at: datetime
    views: int = Field(ge=0)
    likes: int = Field(ge=0)
    comments: int = Field(ge=0)
    shares: int = Field(ge=0)
    is_ad: bool = False
    
class CommentStats(BaseModel):
    n_comments: int = Field(ge=0)
    thai_share: float = Field(ge=0, le=1)
    purchase_intent_rate: float = Field(ge=0, le=1)
    duplicate_rate: float = Field(ge=0, le=1)
    emoji_only_rate: float = Field(ge=0, le=1)


class Creator(BaseModel):
    handle: str                      
    nickname: str = ""
    bio: str = ""                    
    followers: int = Field(ge=0)
    tier: Tier
    profile_url: str
    region: str | None = None
    scraped_at: datetime

class Candidate(BaseModel):
    """A creator found by discovery, with the queries that found them and the matching clips."""
    creator: Creator
    found_by: list[str]
    videos: list[Video]


class Discovery(BaseModel):
    candidates: list[Candidate]
    queries: list[str]
    errors: dict[str, str] = {}           # query -> error message
    videos_seen: int = 0
    non_thai_filtered: int = 0            # creators dropped by the Thai filter
