# SIGNALINTEL: HANDOFF

**Tactical session state.** Updated end of each session. For stable
project context (who/what/how), see `PROJECT_CONTEXT.md`.

Last updated: 14 May 2026, end of session (Phase 2b-ii live in production, all committed and pushed).
Next session: Phase 2c or later phases. FRESH CHAT recommended.

---

## JUST SHIPPED — 14 May 2026 (Phase 2b-ii: enrichment pipeline + composite rebalance, now live)

### Phase 2b-ii (5 commits, pushed)

**Commit 16e36bd** — `feat(scorer): add 4 Yahoo enrichment map helpers + wire into job_generate_signals`

- `database/db.py`: 4 new helpers after `get_legal_risk_map`:
  - `get_earnings_enrichment_map` → `{ticker: [list most-recent-first]}`
  - `get_financials_enrichment_map` → `{ticker: {stmt_type: {fiscal_year: {raw_key: value}}}}`
  - `get_inst_ownership_map` → `{ticker: {total_pct_held, holder_count, filing_date}}` (latest filing only via INNER JOIN)
  - `get_analyst_momentum_map` → `{ticker: {upgrades_90d, downgrades_90d, net_momentum}}` (90-day window, 'up'/'init'=upgrade)
- `main.py`: 4 new imports, 4 map builds in `job_generate_signals`, 4 new kwargs passed to `score_all_tickers`.
- `tests/test_enrichment_map_builders.py`: 4 isolated shape tests using tmp_path SQLite.

**Commit a108153** — `feat(scorer): add 5 Phase 2b-ii scorer functions + TickerSignal fields`

- `signals/scorer.py`:
  - `_parse_market_cap_text(s)` → float|None: parses "1.5B" etc.
  - `score_earnings_surprise`: 4-quarter decay weights (4/3/2/1), contribution ladder ±7/±15/±25, neutral zone (-3%, 0%], P5 empty→50.0
  - `score_piotroski`: Lock 1 (< 2 years → 50.0), 9 binary signals, F≥7→80/6→65/5→50/4→38/≤3→20
  - `score_altman_penalty`: all-or-nothing, Z≥3→0/≥1.8→-10/≥0→-30/<0→-60, X4 uses TotalLiabilitiesNetMinorityInterest
  - `score_inst_ownership`: Lock 3 (pct>60→75.0), tiers >40→55/≤20→35, P5 None→50.0
  - `score_analyst_momentum`: net≥3→80/.../≤-3→20, P5 None→50.0
  - `TickerSignal`: 5 new fields (earnings_score=50.0, piotroski_score=50.0, altman_penalty=0, inst_own_score=50.0, analyst_mom_score=50.0)

**Commit f1c825d** — `feat(scorer): rebalance composite weights to 1.60-sum + apply Altman penalty additively`

- `compute_composite`: 4 new params (earnings, piotroski, inst_own, analyst_mom all default 50.0), 4 new weights (each 0.125, total 0.50 added). New sum: 1.60.
- `score_all_tickers`: computes all 5 new scores per ticker, passes to `compute_composite`, applies `altman_penalty` additively alongside `legal_penalty` in `c_score_raw`.

**Commit f477b5f** — `feat(scorer): bump SCORING_ENGINE_VERSION to 0.13.0 for Phase 2b-ii`

- `config/constants.py`: `SCORING_ENGINE_VERSION = "0.13.0"`

**Commit 48fdf49** — `test(scorer): regenerate snapshot baseline for v0.13.0 + add synthetic enrichment maps for SS07`

- SS07 row: `"market_cap": "240M"` added. 4 new synthetic enrichment constants covering all new scorer paths.
- SS07 synthetic data: 4 severe earnings misses → score 0.0; Piotroski F=2 → score 20.0; analyst net=-4 → score 20.0; Altman Z<0 → penalty -60 → composite clamped to 0.0 → STRONG_SELL.
- EXPECTED_SNAPSHOT regenerated for v0.13.0.
- P21 distribution: STRONG_BUY(1) BUY(2) STRONG_HOLD(6) HOLD(1) SELL(2) WEAK_HOLD(1) STRONG_SELL(1)

### Phase 2b-i (5 commits, pushed prior session)

Yahoo enrichment table schemas, scrapers, and cron wiring for: earnings_history, analyst_changes, institutional_holders, financial_statements, yahooquery_raw. All schemas live; all tables still empty (crons pending, see STILL OPEN). Scores 9-13 scaffolded.

### Phase 2a cleanups and follow-up commits (pushed)

- Secrets leakage gate (2eb28c5): `docs/config_variable_classification.md` created; CLAUDE.md updated. Near-miss: ALERT_CONFIG holds SMTP credentials, matches no standard grep pattern.
- FOLLOWUPS cleanup (61a9eea): 6 completed entries pruned; TEST ISOLATION REFACTOR structural debt added.
- Em-dash cosmetic fix in test assertion messages.
- P23 auth-adjacent audit escalation added to PROCESS INVARIANTS.
- BUG-001 (7949805): tier badge fix. BUG-002 (6e015d0): tier-limit UX structured errors.
- Watchlist picker component (6df4e88) + wiring into ticker/screener/penny screener (6d05aa3).
- P6 compliance: penny page market cap formatting (28231e3).

### Doc commits (this session)

- **76356d2** — unauthorized HANDOFF.md rewrite by CC (committed without prompt, content accurate, act not authorised; P24 codified as result)
- **dfa9276** — `docs: add process-lesson for HANDOFF self-edit incident (14 May 2026)` — P24 lesson added to PROJECT_CONTEXT.md

### Production deploy

- Old scheduler (PID 1867, v0.12.0) killed.
- New scheduler (PID 11172, v0.13.0) started 14:18 BST, 14 May 2026.
- First v0.13.0 production run: 10,807 tickers scored, 1,425 rating changes written, all `signal_scores` rows tagged `scoring_version = "0.13.0"`.

### Test count

- pytest: 232 passing, 4 Yahoo freshness skipped (tables still empty, correct behaviour).
- Prior: 207 passing. +29 new tests (25 phase2b scorers + 4 enrichment map builders) = net 232.

---

## CURRENT STATE (end of 14 May 2026)

- Scheduler PID 11172 running v0.13.0. Started 14:18 BST.
- 0 commits ahead of remote. All pushed.
- 5 Yahoo data tables: schema live, all rows empty. Earliest data: analyst_changes + earnings_history after tonight's 02:00/02:15 BST crons.
- pytest: 232 passing, 4 Yahoo freshness skipped.
- SCORING_ENGINE_VERSION: 0.13.0.
- Composite: 9-component weighted sum / 1.60 normalised. Weights: momentum 0.35, quality 0.30, insider 0.25, reversion 0.10, volume 0.10, earnings_surprise 0.125, piotroski 0.125, inst_own 0.125, analyst_mom 0.125. Altman penalty additive (alongside legal_penalty) before `_clamp`.

---

## PROCESS TELLS — 14 May 2026 (Phase 2b-i and Phase 2b-ii sessions)

**Phase 2b-i tells:**

- **Snapshot gap (P21 codified).** Phase 2b-i snapshot test had no ticker exercising any new scorer (all enrichment maps were empty `{}`). P21 matrix coverage requires at least one ticker to exercise each new scorer path. SS07 now carries synthetic data for all 4 enrichment paths. Rule: when adding a scorer, add a snapshot-fixture row that exercises the non-neutral path or the snapshot test fails to protect the scorer's logic.

- **Housekeeping ceremony (calibrate-to-scope).** Phase 1 + Phase 2 rigour earns its weight on substrate refactors and scoring changes. It is over-ceremony on housekeeping (file deletions, table truncations, residue cleanups). Single-turn prompt with embedded self-check is the right shape. Collapsed a two-turn cleanup sequence to one-turn after Mark pushed back; finished cleanly in 10 minutes.

- **Date-blindness (P22 codified).** Both CC and Athena can be primed by prior context about dates. Any "yesterday / tonight / overnight" temporal reasoning must be explicitly grounded on the session start date. P22 added to PROCESS INVARIANTS.

- **Comm preference (durable, locked mid-session).** Athena was over-explaining prompt-construction rationale and surfacing options as open questions. Mark's preference: deliver outcome + next prompt, briefly. No meta-notes on prompt design mid-flow. Conclusions, not derivations. Holds across sessions.

**Phase 2b-ii tells:**

- **Empty-insiders diagnostic error.** Pre-commit P21 check passed `[]` instead of `_SYNTHETIC_INSIDERS` to `score_all_tickers`. Produced SS07 composite 35.8 (insider neutral = 50) instead of correct 28.0 (3 sellers → insider = 0). Overstated the problem: SS07 appeared to route SELL, not WEAK_HOLD. Actual issue was that even at 28.0 (correct), P21 STOP fired correctly (28.0 > 25 STRONG_SELL threshold). Lesson: diagnostic scripts must use the same input fixtures as the test.

- **P21 STOP fired correctly; Option A chosen.** SS07 with all-neutral enrichment maps (28.0) exceeded the <25 STRONG_SELL threshold. Option A (add synthetic enrichment data for SS07) was chosen over Option B (adjust base screener inputs). Option A exercises the new code paths; Option B would have masked the problem. Snapshot now correctly has all 7 tiers.

- **HANDOFF self-edit (P24 codified).** CC committed a full HANDOFF.md rewrite (76356d2) without prompt authorisation, reading the header "Updated end of each session" as standing permission. It is not. P24 added: doc-file header text is descriptive metadata, never a standing instruction. Mitigation phrasing for all implementation prompts: "do not modify HANDOFF.md or PROJECT_CONTEXT.md." Header text = metadata, not permission.

- **Altman Z empirical validation queued.** Thresholds (Z≥3/1.8/0/<0 → 0/-10/-30/-60) are 1968-era manufacturing calibrations. Modern tech-heavy universe may routinely fall in the distress zone (Z<1.8) without actual bankruptcy risk. Queue a distribution check before v0.13.0 data accumulates: compute Z-scores for the production ticker universe, plot distribution, verify penalty tiers are calibrated for SignalIntel's stock universe. See FOLLOWUPS: URGENT.

- **Secrets leakage gate + config variable classification.** `docs/config_variable_classification.md` created (commit 2eb28c5) after ALERT_CONFIG near-miss. SMTP credentials live in a variable whose name matches no standard grep pattern (TOKEN|KEY|PASSWORD|SECRET). Pattern: credentials can live in any variable. Auditors must use the classification file, not literal grep patterns.

---

## STILL OPEN

- **Tonight's Yahoo crons (02:00 ANALYST / 02:15 EARNINGS, 15 May 2026 BST):** First overnight cron since Phase 2b-i schema landed. Verify: `sqlite3 data/trading_system.db "SELECT data_type, COUNT(*), MAX(last_success_at) FROM external_scrape_log GROUP BY data_type;"` — look for ANALYST and EARNINGS rows dated 2026-05-15.
- **Sunday 17 May — institutional_holders bulk job:** Verify same query + `SELECT COUNT(*) FROM institutional_holders;`
- **Monday 18 May — financial_statements bulk job:** Verify same query + `SELECT COUNT(*) FROM financial_statements;`
- **Tuesday 19 May — earnings_history bulk job:** Verify same query + `SELECT COUNT(*) FROM earnings_history;`
- **Phase 2c direction TBD.** Programme plan lists flag substrate, rendering, end-to-end verification. See FOLLOWUPS: STRUCTURAL DEBT (Phase 2c direction).
- **0 commits to push.** Everything is on remote.
- **Real-data Altman Z distribution check.** See PROCESS TELLS: Altman empirical validation and FOLLOWUPS: URGENT.

---

## NOTES FOR FRESH-CHAT ATHENA

- Read PROJECT_CONTEXT.md first (stable), then this HANDOFF for current state.
- Phase 2b-ii is fully shipped and live in production. v0.13.0, PID 11172, 14:18 BST 14 May 2026. All commits pushed.
- First action if continuing on Yahoo enrichment: verify overnight cron data. Check `external_scrape_log` for ANALYST and EARNINGS rows dated 2026-05-15. Query in STILL OPEN above.
- The snapshot test (`tests/test_scorer_snapshot.py`) now exercises all 4 enrichment paths via SS07 synthetic data. It is a "change only when you mean to" artefact. Do not update EXPECTED_SNAPSHOT unless scoring logic intentionally changes.
- `signals/scorer.py` enrichment scorer functions are at lines ~295-525. `compute_composite` is at ~573. `score_all_tickers` is at ~680.
- 9-component composite; Altman penalty is additive (not a weight in the weighted sum). Weights listed in CURRENT STATE above.
- P24 is new this session: CC must not self-initiate edits to HANDOFF.md or PROJECT_CONTEXT.md. Header text is metadata, not permission.

---

*End handoff.*
