# Query-driven evidence selection benchmark

This offline development benchmark calls the public PreparedContextBuilder. It does not replace the contest acceptance suite or provide evidence labels to product code.

## Public data provenance

David Wadden, Shanchuan Lin, Kyle Lo, Lucy Lu Wang, Madeleine van Zuylen, Arman Cohan, and Hannaneh Hajishirzi, *Fact or Fiction: Verifying Scientific Claims* (EMNLP 2020).

The [SciFact README](https://github.com/allenai/scifact/blob/68b98a56d93e0f9da0d2aab4e6c3294699a0f72e/README.md) identifies the [public archive](https://scifact.s3-us-west-2.amazonaws.com/release/latest/data.tar.gz). Its [schema](https://github.com/allenai/scifact/blob/68b98a56d93e0f9da0d2aab4e6c3294699a0f72e/doc/data.md) describes corpus abstracts as sentence arrays and evidence as sentence indexes.

Claims/evidence use [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/); abstracts belong to S2ORC and use [ODC-By 1.0](https://opendatacommons.org/licenses/by/1-0/), as stated in the [source license](https://github.com/allenai/scifact/blob/68b98a56d93e0f9da0d2aab4e6c3294699a0f72e/LICENSE.md). Downloaded data stays outside Git under `.artifacts/evidence-selection/source/`.

Pinned archive SHA-256: `11c621288d41ac144d29b13b0f8503b3820b7d6e8b1f6ff24dff335c196d76be` (S3 version `8LiW3OUBLBvhehDzrRdGD1ibIaXL.6PQ`, modified 2021-01-26).

| File | Rows | SHA-256 |
| --- | ---: | --- |
| corpus.jsonl | 5183 | b8d6c89624cb2ed74dee8938effc4f5d8bd2086887880af8110d64be4ceade62 |
| claims_train.jsonl | 809 | f4c8fa82d8bd0653a9cc8d61a6ea48c25eacea64e90af5dbf390ebb1b74372f0 |
| claims_dev.jsonl | 300 | 86f0435d08fdb65d1aa41d1472684f57e6e71930626497bdf4d7a9ec1a632217 |

Only corpus/train/dev files are extracted. Public train has 468 single-gold-document claims, of which 315 remain after excluding gold documents referenced by dev claims. Public dev has 178 single-gold-document claims before budget feasibility filtering. No test labels or hidden contest inputs are used.

## Case construction and evaluation

Join abstract sentences with one ASCII space, preserving each original sentence and recording its Python-character offset. Union all annotated evidence sentence indexes for the one gold document. A case contains that abstract and the two highest query-BM25 non-gold abstracts; stable claim-text hashes determine candidate order. Reject a claim from both budgets if complete gold plus exact citations and envelope cannot fit either 1400 or 1800 bytes, recording the reason. Keep every eligible held-out claim; never select cases by achieved score.

Tune only on train. Keep dev untouched until parameters are frozen; after using dev feedback for tuning it is no longer an independent validation set. The scorer merges selected intervals in the gold document, counts lowercase `[a-z]+|\d+` token multisets, and separately checks full coverage of every gold interval. Product calls receive query, original candidate texts/identities, and budget only.

Development gates use at least 24 held-out claims at both budgets, mean token recall >= 0.65, and complete-coverage proportion >= 0.40. Results from this constructed workload are development evidence, not an official contest score.

## Environment and official acceptance

Use Python 3.12 with this checkout's locked dependencies. The contest-specific public fixed-environment configuration has not been located. Upstream `2026-competition` points to frozen commit `9db1753434162c0aa7c9f1189a1722e617880a52`; `2026-competition-ecosystem` at `d647275c308e09b7b623f322a26f5c4e85734fb0` contains ecosystem placeholders. Neither inspected tree contains the challenge-10 acceptance directory.

## Run the development benchmark

```bash
uv run python -m benchmark.evidence_selection.prepare --source-root .artifacts/evidence-selection/source --output-dir .artifacts/evidence-selection/cases
uv run python -m benchmark.evidence_selection.evaluate --cases .artifacts/evidence-selection/cases/train.jsonl --output .artifacts/evidence-selection/train.json
uv run python -m benchmark.evidence_selection.evaluate --cases .artifacts/evidence-selection/cases/validation.jsonl --output .artifacts/evidence-selection/validation.json
```

With the pinned source, preparation yields 257 train claims and 148 validation claims. The first frozen validation run has mean token recall 0.623187 and complete coverage 125/296: the recall development gate fails. Runtime integration has not started. The first validation result is retained; this set must not be represented as independent validation after using its feedback for development. Full results and limitations are documented in `docs/powercontext-contest/results/10-evidence-selection.md`.

## Supplementary validation protocol

Use the explicit supplementary mode to isolate claims not used in the original experiments:

```bash
uv run python -m benchmark.evidence_selection.prepare --source-root .artifacts/evidence-selection/source --output-dir .artifacts/evidence-selection/supplementary --supplementary --previous-cases-dir .artifacts/evidence-selection/cases
```

This mode considers only unused multi-document evidence claims, sorted by claim ID. It excludes a complete claim if any official supporting document was a gold document in the previous cases. It then removes duplicate normalized query text, chooses the lexicographically first supporting document, and excludes all supporting documents from negative selection. All evidence intervals in the chosen document are retained; infeasibility at either budget removes the complete two-request claim. Fixed preparation produces 32 claims/64 requests, excluding 9 claims for gold overlap and 6 for budget feasibility. The manifest records previous case hashes and the selector hash before changes. The original train/validation output is unchanged.

These claims and selected gold documents were not used for previous parameter selection. Their text can still occur in the public negative-retrieval corpus. This is a supplementary public development validation workload, not the hidden official contest set.

## Current global objective results

The selector uses global document-weighted TF saturation with k1=10, cap=8, b=0, document prior base=0, window limit=32, and beam width=32. Parameters were selected only on the original 257 training claims; k1=10/cap=16 was rejected because it failed the repeated-citation novelty regression. Training recall is 0.663240 and complete coverage is 0.540856 over 514 requests.

The frozen supplementary workload's first independent evaluation fails both development gates: recall 0.281492 and complete coverage 12/64=0.187500. At 1400 bytes the figures are 0.232931/0.125000; at 1800 bytes, 0.330053/0.250000. Only 28/64 requests select the designated gold document. Runtime integration remains gated. This multi-document-evidence workload has a different construction from the original single-gold validation; the quality difference does not by itself measure an algorithm regression. Neither workload is official acceptance.

Frozen supplementary SHA-256: `f59d1a8466719e24544f38e0a69111ad544039ced85093ce3e0f4a2f5ed94ebd`. Frozen selector SHA-256: `775dd29d0c290dedcba319b4a8cb1c79a46650c1f09ad9895349875c1da9b047`. Preserve `global-parameter-freeze.json`, `global-supplementary.json`, and `supplementary/manifest.json` under the ignored artifact directory. Re-evaluating used cases is reproduction, not fresh independent validation.

```bash
uv run python -m benchmark.evidence_selection.evaluate --cases .artifacts/evidence-selection/supplementary/supplementary.jsonl --output .artifacts/evidence-selection/supplementary-reproduction.json
```

## Paired reproduction of both selector versions

The pre-global selector snapshot was recovered byte-for-byte from this task's source-read record and verified against its frozen SHA-256 `a376bb66ddc0ac37c2590a2c10aaac5df9062f703023f81c38b6d48455b0ec1d`. Both selectors were rerun on identical train/validation/supplementary cases and budgets. Old train/validation outcomes reproduce every saved per-request metric and selected-entry record; current outcomes also reproduce existing records. Product source files were not overwritten.

| Dataset | Requests | Recall before → current | Complete before → current |
| --- | ---: | ---: | ---: |
| Train | 514 | 0.654529 → 0.663240 | 0.498054 → 0.540856 |
| Original validation | 296 | 0.623187 → 0.640626 | 0.422297 → 0.466216 |
| Supplementary | 64 | 0.280397 → 0.281492 | 0.171875 → 0.187500 |

Aggregate gains coexist with individual regressions. In supplementary 1800-byte requests, recall drops from 0.330396 to 0.330053 while complete coverage rises from 0.21875 to 0.25. Supplementary low recall already existed in the old selector. Detailed paired records are in ignored `reproduction-comparison.json` and the contest result document. These are diagnostic replays, not fresh independent validation or official scores.

## Local cost reproduction

```bash
uv run python -m benchmark.evidence_selection.measure --cases .artifacts/evidence-selection/cases/train.jsonl --output .artifacts/evidence-selection/global-costs.json --budget 1800 --repeats 3
```

The command records input hashes/templates, query, budget, Python/platform, one warm-up, all timing samples, a separate tracemalloc run, selector/script/case hashes, and window/extension diagnostics. Three timing samples provide only a rough local measurement; they do not establish a production SLA. Generated-window counts include duplicates before pruning; extension counts include the omission probe.

## Sentence/context experiment: not adopted

The fixed training ladder evaluated 22 configurations including baseline. Best experimental recall was 0.671387 versus baseline 0.663240, but complete coverage fell from 278/514 (0.540856) to 258/514 (0.501946), with regression at both budgets. No configuration passed the predeclared adoption gate; all three grouped training folds retained baseline. New dev/supp evaluation was not run.

The prototype is archived locally with source/test hashes and an isolated replay script under `.superpowers/sdd/2026-10-25-10-sentence-evidence/`. Production selector SHA is restored to `775dd29d0c290dedcba319b4a8cb1c79a46650c1f09ad9895349875c1da9b047`. `sentence-scoring/training-selection.json` and paired reports retain all failures. The result document and `docs/superpowers/reviews/2026-10-25-10-sentence-scoring-review.md` distinguish prototype tests (147), restored-baseline focused tests (132), and contract checks (49). No official acceptance or full-suite success is claimed.

Paired-report CLI:

```bash
uv run python -m benchmark.evidence_selection.compare --cases .artifacts/evidence-selection/cases/train.jsonl --before .artifacts/evidence-selection/sentence-scoring/baseline-train.json --after .artifacts/evidence-selection/sentence-scoring/abbreviations-w0.5-b1.0-train.json --output .artifacts/evidence-selection/sentence-scoring/paired-reproduction.json
```
