
from pydantic import BaseModel, Field

from kol_matcher.schemas import UtcDatetime
from kol_matcher.schemas.creator import (
    CommentStats,
    Creator,
    Evaluation,
    Shortlist,
    Tier,
    Video,
)

# Tool outputs that are not core models already. Scraped text (bio, nickname, caption) is
# returned in its own fields: it is data from third parties, never instructions.


class ClipSummary(BaseModel):
    url: str
    caption: str          # scraped text, truncated
    views: int


class CandidateSummary(BaseModel):
    handle: str
    nickname: str         # scraped text
    bio: str              # scraped text
    profile_url: str
    tier: Tier
    followers: int
    found_by: list[str]   # queries that found this creator
    sample_clips: list[ClipSummary]


class SearchResult(BaseModel):
    profile_hash: str
    queries: list[str]
    candidates: list[CandidateSummary]   # pre-ordered by queries matched, followers, clips
    errors: dict[str, str] = {}
    videos_seen: int
    non_thai_filtered: int


class FollowerPoint(BaseModel):
    scraped_at: UtcDatetime
    followers: int


class CreatorDetail(BaseModel):
    creator: Creator
    follower_history: list[FollowerPoint]   # one point per observation, oldest first
    videos: list[Video]                     # newest first; captions are scraped text
    comment_stats: CommentStats | None = None


class ShortlistResult(Shortlist):
    csv: str


class RankedHandle(BaseModel):
    rank: int
    handle: str
    profile_url: str
    tier: Tier
    total: float


class EvaluationResult(Evaluation):
    """Evaluation with `creators` cut to the top N, to keep the AI client's context small.
    The rest of the ranking is listed in `more` without metrics or evidence."""
    more: list[RankedHandle] = Field(default_factory=list)   # ranked below the top N, best first
