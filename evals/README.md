# Evals

There is no labelled ground truth for "good matches", so the matcher is measured against a proxy:
**creators a brand has already worked with**. A good matcher, told only what the brand sells and who
it targets (never its name), should rank those creators high.

## 1. Ground truth

```bash
uv run kol-matcher ground-truth --brand-term <brand> --exclude-handle <brand's own account> \
    --out evals/ground_truth/<brand>.csv
```

Searches TikTok for the brand name and its hashtag, and keeps Thai creators whose clips mention the
brand. `sponsored` is true when TikTok marks the clip as an ad or the caption carries a paid tag
(`#ad`, `#โฆษณา`, ...). Open the CSV and set `keep` to `n` for rows that are not collaborations
(resellers, shops, unrelated mentions). Only kept rows count.

## 2. Backtest

```bash
uv run kol-matcher backtest --profile evals/profiles/<brand>.json \
    --ground-truth evals/ground_truth/<brand>.csv --k 20 --pool 40 --out evals/results/<run>.json
```

- Every query that names the brand (from the profile or `--query`) is dropped and reported, so the
  ground truth is not found by construction. Add other spellings with `--brand-term`.
- The first `--pool` discovered creators are evaluated and ranked in one call.
- Scrapes are cached for 30 days, so a rerun is free and gives the same numbers.

Reported:

| Field | Meaning |
|---|---|
| `recall_at_k` | Ground-truth creators in the top K / all ground-truth creators |
| `gt_discovered` | Ground truth found by search at all: the ceiling for recall |
| `hits` vs `discovery_order_hits` | Top K by score vs top K in search order, same pool: what ranking adds |
| `random_hits` | Expected hits for K picked at random from the pool |
| `seconds`, `apify_runs`, `est_cost_usd` | Run cost (estimate; the Apify console has the exact charge) |

## 3. Query-source experiment

Run the backtest twice with the same profile: once with the default queries (profile keywords),
once with queries an AI client proposed (`--query` repeated). For AI queries, ask the client three
times in fresh chats and run each set, to measure variance.

## 4. Baseline: AI client + raw Apify

1. In a fresh chat with only Apify's MCP server connected (no kol-matcher), give the same brief:
   the profile JSON without the brand name, and "find the 20 best Thai TikTok creators for this
   brand, ranked". Note the time and the Apify charge.
2. Save the ranked handles, one per line, to `evals/results/baseline_<n>.txt`.
3. Score it: `uv run kol-matcher score-list --ground-truth evals/ground_truth/<brand>.csv --handles evals/results/baseline_<n>.txt --k 20`
4. Repeat three times for consistency.

## Caveats

- Ground truth is incomplete: collaborations without a brand mention in the caption are missed.
- Brand mentions include fans and resellers; manual review of `keep` matters.
- A creator who mentions the brand often also posts about its category, so recall partly measures
  niche match, which is the goal, but it is not proof of campaign performance.

## Results: Srichand (2026-10-24)

Profile `evals/profiles/srichand.json` (written by hand, as an AI client would; brand name never
searched). Ground truth: 47 Thai creators mention Srichand; 9 dropped on review (brand accounts, an
intern post, a Khon Kaen milk shop with the same Thai name, resellers, a fan post): **38 kept**.

| Run | Pool | Ground truth in pool | Hits@10: ranker / search order / random | Apify |
|---|---|---|---|---|
| Default queries (6) | 20 of 123 found | 0 (search found 1 of 38) | 0 / 0 / 0 | $3.35 |
| Same + 8 seeded ground-truth creators | 27 ranked | 8 | 3 / 0 / 2.96 | $0.89 |

Findings:

- **Discovery is the bottleneck.** Six generic beauty queries found 123 Thai creators, only one of
  them a known collaborator. Recall@K is capped by search, not ranking.
- **Ranking does not separate known collaborators from other beauty creators** (3 hits vs 2.96
  random; their ranks 4, 6, 10, 20, 21, 24, 25, 27). Every creator in the pool is already a beauty
  creator, and clip-to-theme similarity is flat across it (top-3 mean 0.49-0.59 for everyone), so
  relevance barely moves the order and performance decides it.
- **The proxy rewards what brands buy, not only fit.** Three of the eight collaborators post mostly
  sponsored clips (sponsored ratio 0.70-0.73), and five have engagement under 3% per view, which
  the credibility and performance scores penalise by design. "Who the brand hired" is not the same as
  "who fits best", so a low score here is partly the intended authenticity check.
- **Off-topic large creators rank high** (a comedy account at 8, a travel account at 12) on
  performance alone. A minimum relevance gate before ranking would remove them.

Eight seeded creators in a pool of 27 is too small to tune weights on without overfitting; treat
these numbers as a diagnosis, not a benchmark.

### Baseline: Claude + raw Apify tools (one run)

Same brief, brand name withheld, Apify's MCP server only. 3 actor runs (6 searches, 6 recent videos
for 14 creators), ~4 minutes, $1.02. Ranked list: `evals/results/baseline_1.txt`.

- **Recall@10: 0/38**, same as this server. With search finding almost none of the ground truth,
  the backtest cannot tell the two apart.
- **3 creators in common** with this server's top 10, including the same #1 (`amnotmyhair`).
- **All 10 baseline picks are on-topic beauty creators**; this server's top 10 had two off-topic
  accounts ranked on performance. The client reading captions judged relevance better than
  embedding similarity on short captions, which supports a minimum relevance gate.
- The baseline was cheaper ($1.02 vs $2.90; the other $0.45 of the $3.35 was the ground-truth search) because it sampled 6 videos per creator instead of 30.
  It is also not reproducible: no fixed scores, and three creators were ranked from search results
  without checking their profiles.

### v2: 10 clips per creator, relevance gate 0.25, pool 12 (live, new Apify account)

| Run | Ranked | Hits@10: ranker / random | Off-topic in top 10 | Apify |
|---|---|---|---|---|
| Default queries, pool 12 | 11 | 0 / 0 | 0 on-topic slots lost; the 2 gated creators fill ranks 10-11 only because the pool is 11 | $0 (cached; ~$0.45 cold) |
| Same + 8 seeded ground truth | 19 | 4 / 4.21 | 0 | $0.71 |

- **Cost:** 20 profiles cost $0.71 at 10 clips vs $2.00 at 30. A full cold run of this backtest
  (6 searches + 12 profiles) is now ~$1.35, close to the raw-Apify baseline ($1.02).
- **Off-topic:** gated creators (comedy, family vlog) now rank after every on-topic creator and
  carry a `low_relevance` flag.
- **Agreement with the baseline:** 4 of its top 10 are in ours (`amnotmyhair`, `ceo.kalmandkind`,
  `amykitiya`, `aum_aemmika19`), and the top two are the same, in the same order.
- **Known collaborators still rank at chance** (4 vs 4.21). As above, "hired by the brand" mixes
  beauty specialists with lifestyle creators bought for reach, so this proxy cannot reward fit alone.
