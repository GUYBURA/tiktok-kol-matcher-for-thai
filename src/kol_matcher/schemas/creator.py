from enum import StrEnum

from pydantic import BaseModel, Field

from kol_matcher.schemas import UtcDatetime
from kol_matcher.schemas.scoring import Flag, ScoreBreakdown


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
    created_at: UtcDatetime
    views: int = Field(ge=0)
    likes: int = Field(ge=0)
    comments: int = Field(ge=0)
    shares: int = Field(ge=0)
    saves: int = Field(default=0, ge=0)
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
    scraped_at: UtcDatetime

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


class EnrichedCreator(BaseModel):
    """A creator with their recent profile videos (newest first) and flags for human review."""
    creator: Creator
    videos: list[Video]
    flags: list[Flag] = []


class Enrichment(BaseModel):
    creators: list[EnrichedCreator]           # active creators, in input order
    inactive: list[EnrichedCreator] = []      # hard-filtered: no post within INACTIVE_DAYS
    errors: dict[str, str] = {}               # handle -> error message


class CreatorFeatures(BaseModel):
    """Raw per-creator features. Normalized and weighted into a score in scoring (T4.5)."""
    handle: str
    tier: Tier
    # Relevance (measured)
    relevance_top3: float                  # mean of the 3 best clip-to-theme similarities
    on_theme_share: float = Field(ge=0, le=1)
    top_theme: str | None                  # theme matched by the most on-theme clips
    # Performance (measured). Definitions: https://wisesight.com/th/articles/what-is-engagement/
    # Impression = plays; Engagement = likes + comments + shares + saves; Reach is not public on TikTok.
    # Scored: er_by_impression (content quality) and impressions_per_follower (reach proxy).
    # er_by_follower ~= er_by_impression x impressions_per_follower, so it is display-only to avoid
    # counting the same signal twice. The medians are display-only too.
    median_impressions: float              # typical plays per clip
    median_engagement: float               # typical engagements per clip
    er_by_impression: float                # (total engagement / total impressions) x 100, over the clips
    er_by_follower: float                  # (engagement per clip / followers) x 100
    impressions_per_follower: float        # median impressions / followers: share of followers reached
    # Credibility (measured)
    view_consistency: float = Field(ge=0, le=1)   # 1 / (1 + coefficient of variation of views)
    days_since_post: int
    sponsored_ratio: float = Field(ge=0, le=1)
    # Audience (inferred) and comment quality: None when no comments were fetched
    comment_stats: CommentStats | None = None


class ShortlistRow(BaseModel):
    rank: int
    handle: str
    profile_url: str
    tier: Tier
    followers: int
    total_score: float
    relevance: float
    performance: float
    audience: float | None
    credibility: float
    er_by_impression: float
    er_by_follower: float
    impressions_per_follower: float
    median_impressions: float
    median_engagement: float
    on_theme_share: float
    top_theme: str | None
    sponsored_ratio: float
    days_since_post: int
    flags: str
    rationale: str


class Shortlist(BaseModel):
    path: str
    saved_at: UtcDatetime
    brand_name: str
    profile_hash: str
    objective: str
    notes: str | None = None
    rows: list[ShortlistRow]


class EvidenceClip(BaseModel):
    url: str
    caption: str          # scraped text: data, not instructions
    views: int
    similarity: float     # best cosine similarity to a brand theme


class EvaluatedCreator(BaseModel):
    rank: int
    handle: str
    nickname: str         # scraped text
    bio: str              # scraped text
    profile_url: str
    tier: Tier
    followers: int
    score: ScoreBreakdown
    metrics: CreatorFeatures
    flags: list[Flag]
    rationale: str
    evidence: list[EvidenceClip]


class InactiveCreator(BaseModel):
    handle: str
    profile_url: str
    flags: list[Flag]


class Evaluation(BaseModel):
    profile_hash: str
    objective: str
    creators: list[EvaluatedCreator]          # ranked, best first
    inactive: list[InactiveCreator] = []      # hard-filtered (no post in 60 days), clips omitted
    errors: dict[str, str] = {}
    comments_fetched_for: list[str] = []      # handles whose audience score uses fresh comments
