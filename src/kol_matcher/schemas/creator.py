from enum import StrEnum
from datetime import datetime
from pydantic import BaseModel, Field

class Tier(StrEnum):
    nano = "nano"
    micro = "micro"
    mid = "mid"
    macro = "macro"
    mega = "mega"
    
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