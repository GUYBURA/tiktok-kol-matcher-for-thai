from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class Objective(StrEnum):
    awareness = "awareness"
    engagement = "engagement"
    conversion = "conversion"

WEIGHTS = {
    Objective.awareness: {"relevance": 0.25, "performance": 0.45, "audience": 0.15, "credibility": 0.15},
    Objective.engagement: {"relevance": 0.35, "performance": 0.30, "audience": 0.20, "credibility": 0.15},
    Objective.conversion: {"relevance": 0.30, "performance": 0.20, "audience": 0.35, "credibility": 0.15},
}

Basis = Literal["measured", "inferred"]

class Metric(BaseModel):
    value: float = Field(ge=0, le=1)
    basis: Basis
    
class ScoreBreakdown(BaseModel):
    relevance: Metric
    performance: Metric
    audience: Metric | None    # None when no comments were fetched; total is then reweighted
    credibility: Metric
    total: float = Field(ge=0, le=1)
    
class FlagType(StrEnum):
    inactive = "inactive"
    brand_safety = "brand_safety"
    competitor_mention = "competitor_mention"
    self_declared_expertise = "self_declared_expertise"
    high_sponsored_ratio = "high_sponsored_ratio"
    shop_account = "shop_account"            # bio reads like a store or brand, not a creator
    work_restriction = "work_restriction"    # bio limits what work the creator takes, e.g. "ไม่รับรีวิวสกินแคร์"
    low_relevance = "low_relevance"          # below MIN_RELEVANCE: ranked after on-topic creators


class Flag(BaseModel):
    type: FlagType
    detail: str