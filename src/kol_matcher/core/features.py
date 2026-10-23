from datetime import UTC, datetime
from statistics import mean, median, pstdev

import numpy as np

from kol_matcher.core.thai import has_thai
from kol_matcher.schemas.brand import BrandProfile
from kol_matcher.schemas.creator import (
    CommentStats,
    CreatorFeatures,
    EnrichedCreator,
    Video,
)
from kol_matcher.sources import embeddings

TOP_K = 3
# Cosine cutoff for "clip is about a brand theme". From the BGE-M3 check: related ~0.60,
# unrelated <= 0.42. Tune with the backtest.
ON_THEME_THRESHOLD = 0.5
PURCHASE_INTENT_PHRASES = [
    "พิกัด", "ซื้อที่ไหน", "ซื้อได้ที่ไหน", "สั่งยังไง", "สั่งซื้อ", "กี่บาท", "ราคาเท่าไหร่",
    "ราคา", "มีขาย", "ขายที่ไหน", "ตะกร้า", "ลิงก์", "ลิ้งค์", "link", "อยากได้",
]


def engagement_of(v: Video) -> int:
    """Engagement per Wisesight: interactions with the content. Public on TikTok: likes, comments,
    shares, saves. Plays are Impressions, not engagement, so they are excluded."""
    return v.likes + v.comments + v.shares + v.saves


def comment_stats(texts: list[str | None]) -> CommentStats:
    """Aggregate comment signals. Empty text (sticker-only replies) counts as emoji-only."""
    n = len(texts)
    if n == 0:
        return CommentStats(n_comments=0, thai_share=0, purchase_intent_rate=0, duplicate_rate=0,
                            emoji_only_rate=0)
    cleaned = [(t or "").strip() for t in texts]
    verbal = [t for t in cleaned if any(ch.isalnum() for ch in t)]
    normalized = [" ".join(t.lower().split()) for t in verbal]
    return CommentStats(
        n_comments=n,
        thai_share=sum(has_thai(t) for t in cleaned) / n,
        purchase_intent_rate=sum(any(p in t for p in PURCHASE_INTENT_PHRASES) for t in normalized) / n,
        duplicate_rate=(1 - len(set(normalized)) / len(normalized)) if normalized else 0,
        emoji_only_rate=(n - len(verbal)) / n,
    )


def pool_features(con, settings, profile: BrandProfile, pool: list[EnrichedCreator],
                  comments: dict[str, list[str | None]] | None = None,
                  now: datetime | None = None) -> list[CreatorFeatures]:
    """Features for every creator in the pool. Captions and themes are embedded in one cached batch."""
    now = now or datetime.now(UTC)
    comments = comments or {}
    themes = profile.content_themes
    captions = sorted({v.caption for e in pool for v in e.videos if v.caption.strip()})
    vectors = embeddings.embed(con, settings, themes + captions)
    theme_vecs = vectors[: len(themes)]
    caption_vec = dict(zip(captions, vectors[len(themes):]))
    return [_features(e, themes, theme_vecs, caption_vec, comments.get(e.creator.handle), now) for e in pool]


def _features(e: EnrichedCreator, themes, theme_vecs, caption_vec, comment_texts, now) -> CreatorFeatures:
    videos = e.videos
    followers = e.creator.followers

    # Relevance: best theme similarity per captioned clip. Clips without a caption are not evidence either way.
    clip_vecs = [caption_vec[v.caption] for v in videos if v.caption.strip()]
    if clip_vecs:
        sims = np.vstack(clip_vecs) @ theme_vecs.T          # clips x themes
        best = sims.max(axis=1)
        top3 = float(np.sort(best)[::-1][:TOP_K].mean())
        on_theme = best >= ON_THEME_THRESHOLD
        share = float(on_theme.mean())
        top_theme = None
        if on_theme.any():
            counts = np.bincount(sims[on_theme].argmax(axis=1), minlength=len(themes))
            top_theme = themes[int(counts.argmax())]
    else:
        top3, share, top_theme = 0.0, 0.0, None

    views = [v.views for v in videos]
    engagement = [engagement_of(v) for v in videos]
    avg = mean(views)
    consistency = 1 / (1 + pstdev(views) / avg) if avg > 0 else 0.0

    return CreatorFeatures(
        handle=e.creator.handle,
        tier=e.creator.tier,
        relevance_top3=max(top3, 0.0),
        on_theme_share=share,
        top_theme=top_theme,
        median_impressions=median(views),
        median_engagement=median(engagement),
        er_by_impression=sum(engagement) / sum(views) * 100 if sum(views) > 0 else 0.0,
        er_by_follower=mean(engagement) / followers * 100 if followers > 0 else 0.0,
        impressions_per_follower=median(views) / followers if followers > 0 else 0.0,
        view_consistency=consistency,
        days_since_post=(now - max(v.created_at for v in videos)).days,
        sponsored_ratio=sum(v.is_ad for v in videos) / len(videos),
        comment_stats=comment_stats(comment_texts) if comment_texts is not None else None,
    )


def evidence_clips(con, settings, profile: BrandProfile, e: EnrichedCreator, k: int = 3
                   ) -> list[tuple[Video, float]]:
    """The creator's k clips closest to any brand theme, best first (embeddings come from the cache)."""
    captioned = [v for v in e.videos if v.caption.strip()]
    if not captioned:
        return []
    themes = profile.content_themes
    vectors = embeddings.embed(con, settings, themes + [v.caption for v in captioned])
    best = (vectors[len(themes):] @ vectors[: len(themes)].T).max(axis=1)
    order = sorted(range(len(captioned)), key=lambda i: (-best[i], captioned[i].id))
    return [(captioned[i], round(float(best[i]), 3)) for i in order[:k]]
