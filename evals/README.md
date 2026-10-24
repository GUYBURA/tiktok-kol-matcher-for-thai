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
| Default queries, pool 12 | 11 | 0 / 0 | 5 gated (`low_relevance`) fill ranks 6-10, because only 6 of 11 passed the gate; 3 of them are beauty creators | $0 (cached; ~$0.45 cold) |
| Same + 8 seeded ground truth | 19 | 4 / 4.21 | 0 | $0.71 |

- **Cost:** 20 profiles cost $0.71 at 10 clips vs $2.00 at 30. A full cold run of this backtest
  (6 searches + 12 profiles) is now ~$1.35, close to the raw-Apify baseline ($1.02).
- **Off-topic:** gated creators now rank after every on-topic creator and carry a `low_relevance`
  flag. With 10 clips the gate also catches some beauty creators whose recent captions are short or
  hashtag-only (false positives), so the flag means "check the clips", not "off-topic".
- **Agreement with the baseline:** 4 of its top 10 are in ours (`amnotmyhair`, `ceo.kalmandkind`,
  `amykitiya`, `aum_aemmika19`), and the top two are the same, in the same order.
- **Known collaborators still rank at chance** (4 vs 4.21). As above, "hired by the brand" mixes
  beauty specialists with lifestyle creators bought for reach, so this proxy cannot reward fit alone.

### AI-proposed queries (one fresh Claude chat, brief only)

Queries: `ราคาถูก เครื่องสำอางเซเว่น แต่งหน้าง่ายๆ`, `รีวิวแป้งฝุ่นคุมมัน รองพื้นคุมมัน`,
`#เครื่องสำอางราคาประหยัด`, `สกินแคร์ผิวมัน อากาศร้อน`, `แต่งหน้าไปเรียน ไปทำงาน มือใหม่`,
`#รีวิวเครื่องสำอางดรักสโตร์`. Same settings as v2 (10 clips, gate 0.25, pool 12, K 10).

| Queries | Thai creators found | Ground truth found | Recall@10 | Gated in top 10 | Mean relevance, top 10 | Time (cold) | Apify |
|---|---|---|---|---|---|---|---|
| Profile defaults | 123 | 1 | 0/38 | 5 | 0.29 | not measured | ~$1.35 cold |
| AI-proposed | 141 | 1 (same: `echopwt`) | 0/38 | 3 | 0.36 | 116 s | $1.27 |

- **AI queries did not reach more collaborators.** Different queries, same single hit: the 38 are a
  thin slice of Thai beauty TikTok, and 6 generic queries are unlikely to land on them.
- **They gave a more on-topic pool** (7 of 11 pass the gate vs 6; mean relevance 0.36 vs 0.29), but
  long multi-phrase queries also pulled lifestyle accounts (travel, food).
- **Little overlap:** 1 creator in common with the default-query top 10, none with the baseline. The
  query source changes who is found far more than ranking changes the order.

Conclusion for this proxy: with brand-mention ground truth, recall@K measures search overlap and is
near zero for every setup tried (this server with either query source, and Claude with raw Apify).
A useful next eval needs either many more queries per brand or a labelled pool (people rating fit).

### Consistency: this server, 3 runs from a fresh scrape

Same profile and settings (v2, default queries, pool 12, K 10). Run 1 searched in the morning;
runs 2 and 3 each used a new empty cache a few hours later, two minutes apart. $2.70 for runs 2-3.

| | Runs 1-2 | Runs 1-3 | Runs 2-3 |
|---|---|---|---|
| Thai creators found by both searches | 87 of 158 | 67 of 172 | 74 of 164 |
| Same creators in the evaluated pool of 12 | 5 | 6 | 7 |
| Same creators in the top 10 | 4 | 5 | 5 |
| Score change for a creator ranked in both | max 0.054 | max 0.054 | 0.000 |

In all three top 10s: `amykitiya`, `aum_aemmika19`, `pimchqnoz`.

- **Scoring is stable; search is not.** A creator scraped minutes apart gets the same score; hours
  apart, within 0.05 (new views and likes). But TikTok returns a different half of the results to
  the same query a few hours later, so the pool of 12 changes, and with it the top 10.
- **Small pool amplifies it.** Only the first 12 of ~120 found are evaluated. A larger pool, or
  reusing the cached search within a campaign (the default 24 h cache does this), would steady it.
- Same-data reruns (cached) are identical, which is what makes a saved shortlist reproducible.

### Fact-check of the raw-Apify baseline's claims

Each claim in the baseline's table checked against the creators' 6 latest clips (the sample the
baseline said it used), scraped a few hours after the baseline run. Like rate = likes / views.

| Claim type | Correct | Wrong | Not found | Partly |
|---|---|---|---|---|
| Followers (10) | 10 | 0 | 0 | 0 |
| Other claims (17) | 10 | 3 | 3 | 1 |

- **Wrong:** like rates for `ceo.kalmandkind` ("5-9%", measured 3.3-6.8%), `maiimust_makeup` ("~9%",
  avg 5.1%), `jueqmolpan` ("~9%", avg 15.4%).
- **Not found:** three single-video figures (`amnotmyhair` 932K views, `janenemail_` 1.5M views,
  `tnimptrsn` 553K views) are not among their latest 10 clips, nor 30 for `amnotmyhair`. They may be
  older search results; they cannot be verified from what the baseline reported.
- **Partly:** `aum_aemmika19` like rate "7-10%" (measured 2.8-22.5%, avg 13%).
- Errors cluster in the three creators the baseline ranked from search results without opening
  their profiles. Sponsored-content and competitor claims all held up.
- This server does not produce such claims: every figure it returns is computed from stored
  scrapes, and `save_shortlist` recomputes them.

### Baseline consistency and fact-check, round 2

Second fresh Claude + raw Apify chat, same prompt: ~4 min, $0.86, 4 actor runs, 21 profiles
checked. Recall@10 0/38. Ranked list: `evals/results/baseline_2.txt`.

- **5 of 10 shared with round 1** (`amnotmyhair`, `amykitiya`, `aum_aemmika19`, `ceo.kalmandkind`,
  `tnimptrsn`), about the same as this server across fresh scrapes (4-5 of 10). Both inherit
  TikTok's changing search results.
- **Fact-check** (latest 10 clips, or 30 where cached): followers 10/10 right; of 12 other figures,
  5 right (e.g. `beamsareeda` 402K views and ~12K saves, exact), 2 partly, 0 wrong, 5 not found.
  This round said it relied on older videos, which is why more figures fall outside the latest clips.
- Two single-video figures appeared in both rounds (`amnotmyhair` 932K views, `tnimptrsn` 553-554K
  views), so they are probably real older clips rather than invented. Treat round 1's "not found"
  as unverifiable, not wrong. The three wrong like rates in round 1 stand.
