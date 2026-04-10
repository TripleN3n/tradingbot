# =============================================================================
# APEX — Adaptive Per-token Execution Strategy Engine
# apex/strategy_scorer.py — Strategy Scoring & Ranking
# Version 3.2 — Absolute scoring for meaningful cross-token comparison
# =============================================================================
# CHANGES FROM v3.1:
#
# calculate_composite_score():
#   Previously normalised metrics relative to the token's OWN result set.
#   When a token had only 1 valid strategy, min==max for every metric,
#   so normalise() always returned 0.5 regardless of actual quality.
#   Result: DOGE (70% WR, 3.12 PF) scored identical to BTC (45.8% WR, 1.26 PF).
#   Tier assignment became meaningless — all single-result tokens clustered at 0.5.
#
#   FIX: Two-level scoring:
#   1. Within-token ranking: relative normalization (unchanged) — used to
#      rank multiple strategies for the SAME token against each other.
#   2. Cross-token tier assignment: absolute normalization against fixed
#      reference ranges derived from strategy spec thresholds and realistic
#      maximums — used to compare one token's best strategy against another's.
#
#   The absolute score is stored as composite_score in the DB and used for
#   tier assignment. The relative score is used only for within-token ranking.
#
# ABS_SCORE_RANGES:
#   min = strategy spec minimum threshold (floor)
#   max = realistic excellent performance ceiling
#   Scores below floor → 0.0, above ceiling → 1.0
# =============================================================================

import pandas as pd
import numpy as np
import logging
import sys
from pathlib import Path
from typing import Optional

from bot.config import (
    SCORING_WEIGHTS, SCORING_MINIMUMS, TIERS,
    TIMEFRAME_TIEBREAKER_PCT, TIMEFRAME_PRIORITY, LOGS,
)

# =============================================================================
# LOGGING
# =============================================================================

logger = logging.getLogger(__name__)


# =============================================================================
# ABSOLUTE SCORING REFERENCE RANGES
# =============================================================================
# min = strategy spec minimum threshold (score = 0.0 at this value)
# max = realistic excellent performance ceiling (score = 1.0 at this value)
# These are intentionally conservative ceilings — very few strategies exceed them.

ABS_SCORE_RANGES = {
    "expectancy":    {"min": 0.0,   "max": 0.05},   # 0% → 5% expectancy per trade
    "win_rate":      {"min": 0.35,  "max": 0.85},   # 35% min → 85% excellent
    "max_drawdown":  {"min": 0.0,   "max": 0.30},   # 0% → 30% max allowed
    "profit_factor": {"min": 1.05,  "max": 5.0},    # 1.05 min → 5.0 excellent
    "sharpe_ratio":  {"min": 0.25,  "max": 10.0},   # 0.25 min → 10.0 excellent
}


# =============================================================================
# NORMALISATION HELPERS
# =============================================================================

def normalise(value: float, min_val: float, max_val: float) -> float:
    """Normalise a value to 0–1 range. Returns 0.5 if range is zero."""
    if max_val == min_val:
        return 0.5
    return max(0.0, min(1.0, (value - min_val) / (max_val - min_val)))


def normalise_absolute(value: float, key: str) -> float:
    """
    Normalise a metric value using absolute reference ranges.
    Tokens that barely pass minimums score near 0.0.
    Excellent performers score near 1.0.
    """
    r = ABS_SCORE_RANGES[key]
    return max(0.0, min(1.0, (value - r["min"]) / (r["max"] - r["min"])))


# =============================================================================
# COMPOSITE SCORE CALCULATION
# =============================================================================

def calculate_composite_score_relative(metrics: dict, all_metrics: list) -> float:
    """
    Calculate relative composite score for within-token strategy ranking.
    Normalises each metric relative to the full result set for this token.
    Used only to rank multiple strategies for the same token.
    Returns 0.5 when there is only one result (acceptable — it's just for ordering).
    """
    expectancies   = [m["expectancy"]    for m in all_metrics]
    win_rates      = [m["win_rate"]      for m in all_metrics]
    drawdowns      = [m["max_drawdown"]  for m in all_metrics]
    profit_factors = [m["profit_factor"] for m in all_metrics]
    sharpes        = [m["sharpe_ratio"]  for m in all_metrics]

    norm_exp = normalise(metrics["expectancy"],    min(expectancies),   max(expectancies))
    norm_wr  = normalise(metrics["win_rate"],      min(win_rates),      max(win_rates))
    norm_dd  = normalise(metrics["max_drawdown"],  min(drawdowns),      max(drawdowns))
    norm_pf  = normalise(metrics["profit_factor"], min(profit_factors), max(profit_factors))
    norm_sh  = normalise(metrics["sharpe_ratio"],  min(sharpes),        max(sharpes))

    return round(
        SCORING_WEIGHTS["expectancy"]    * norm_exp +
        SCORING_WEIGHTS["win_rate"]      * norm_wr +
        SCORING_WEIGHTS["max_drawdown"]  * (1.0 - norm_dd) +
        SCORING_WEIGHTS["profit_factor"] * norm_pf +
        SCORING_WEIGHTS["sharpe_ratio"]  * norm_sh,
        6
    )


def calculate_composite_score_absolute(metrics: dict) -> float:
    """
    Calculate absolute composite score for cross-token tier assignment.

    FIX: Uses fixed reference ranges so the score reflects actual quality,
    not just relative rank within a token's own result set.
    A token with 1 excellent strategy now scores meaningfully higher than
    a token with 1 barely-passing strategy.

    This is what gets stored in strategy_assignments.composite_score
    and used for tier assignment.
    """
    norm_exp = normalise_absolute(metrics["expectancy"],    "expectancy")
    norm_wr  = normalise_absolute(metrics["win_rate"],      "win_rate")
    norm_dd  = normalise_absolute(metrics["max_drawdown"],  "max_drawdown")
    norm_pf  = normalise_absolute(metrics["profit_factor"], "profit_factor")
    norm_sh  = normalise_absolute(metrics["sharpe_ratio"],  "sharpe_ratio")

    return round(
        SCORING_WEIGHTS["expectancy"]    * norm_exp +
        SCORING_WEIGHTS["win_rate"]      * norm_wr +
        SCORING_WEIGHTS["max_drawdown"]  * (1.0 - norm_dd) +
        SCORING_WEIGHTS["profit_factor"] * norm_pf +
        SCORING_WEIGHTS["sharpe_ratio"]  * norm_sh,
        6
    )


# For backward compatibility — the assigner reads composite_score
def calculate_composite_score(metrics: dict, all_metrics: list) -> float:
    """
    Kept for backward compatibility.
    Returns relative score (used for within-token ranking only).
    Cross-token scoring now uses calculate_composite_score_absolute().
    """
    return calculate_composite_score_relative(metrics, all_metrics)


# =============================================================================
# MINIMUM THRESHOLD ENFORCEMENT
# =============================================================================

def passes_minimum_thresholds(metrics: dict) -> bool:
    """
    Hard gate — strategy must pass ALL minimums on validation metrics.
    Returns False if any single metric fails.
    """
    checks = [
        ("expectancy",    metrics["expectancy"]    >= SCORING_MINIMUMS["expectancy"]),
        ("win_rate",      metrics["win_rate"]       >= SCORING_MINIMUMS["win_rate"]),
        ("max_drawdown",  metrics["max_drawdown"]   <= SCORING_MINIMUMS["max_drawdown"]),
        ("profit_factor", metrics["profit_factor"]  >= SCORING_MINIMUMS["profit_factor"]),
        ("sharpe_ratio",  metrics["sharpe_ratio"]   >= SCORING_MINIMUMS["sharpe_ratio"]),
    ]

    for name, passed in checks:
        if not passed:
            return False

    return True


# =============================================================================
# TIMEFRAME TIEBREAKER
# =============================================================================

def apply_timeframe_tiebreaker(ranked: list) -> list:
    """
    Apply timeframe tiebreaker rule:
    If top 2 strategies have scores within TIMEFRAME_TIEBREAKER_PCT,
    prefer the higher timeframe. If gap > threshold, keep highest score.
    """
    if len(ranked) < 2:
        return ranked

    best   = ranked[0]
    second = ranked[1]

    best_score   = best["composite_score"]
    second_score = second["composite_score"]

    if best_score == 0:
        return ranked

    gap = abs(best_score - second_score) / best_score

    if gap <= TIMEFRAME_TIEBREAKER_PCT:
        best_tf_priority   = TIMEFRAME_PRIORITY.index(best["timeframe"])   if best["timeframe"]   in TIMEFRAME_PRIORITY else -1
        second_tf_priority = TIMEFRAME_PRIORITY.index(second["timeframe"]) if second["timeframe"] in TIMEFRAME_PRIORITY else -1

        if second_tf_priority > best_tf_priority:
            logger.debug(
                f"Tiebreaker: {second['timeframe']} preferred over "
                f"{best['timeframe']} (gap: {gap:.2%})"
            )
            ranked[0], ranked[1] = ranked[1], ranked[0]

    return ranked


# =============================================================================
# GLOBAL TIER ASSIGNMENT
# =============================================================================

def assign_tiers_globally(scored: dict) -> dict:
    """
    Assign tiers based on each token's best ABSOLUTE composite score
    relative to ALL tokens.

    FIX: Uses absolute_score (not relative composite_score) for tier boundaries.
    This ensures tier boundaries are meaningful — a token with genuinely
    good metrics (high WR, PF, Sharpe) is tier1, not just "best among a
    cluster of 0.5 scores".

    Tier 1 (High):   top 25% of all token absolute scores
    Tier 2 (Medium): 50th–75th percentile
    Tier 3 (Low):    25th–50th percentile
    Below 25th:      excluded from trading
    """
    best_abs_scores = {
        symbol: results[0]["absolute_score"]
        for symbol, results in scored.items()
        if results and "absolute_score" in results[0]
    }

    if not best_abs_scores:
        logger.warning("No tokens with valid strategies — cannot assign tiers")
        return scored

    all_score_values = list(best_abs_scores.values())
    p25, p50, p75    = np.percentile(all_score_values, [25, 50, 75])

    tier_counts = {"tier1": 0, "tier2": 0, "tier3": 0, "excluded": 0}

    for symbol, results in scored.items():
        if not results:
            continue

        score = results[0].get("absolute_score", results[0]["composite_score"])

        if score >= p75:
            tier = "tier1"
        elif score >= p50:
            tier = "tier2"
        elif score >= p25:
            tier = "tier3"
        else:
            tier = None  # Below 25th percentile — exclude from trading

        if tier is None:
            scored[symbol] = []
            tier_counts["excluded"] += 1
        else:
            results[0]["assigned_tier"] = tier
            tier_counts[tier] += 1

    logger.info(
        f"Global tier assignment complete | "
        f"p25={p25:.4f} p50={p50:.4f} p75={p75:.4f} | "
        f"Tier1={tier_counts['tier1']} Tier2={tier_counts['tier2']} "
        f"Tier3={tier_counts['tier3']} Excluded={tier_counts['excluded']}"
    )

    return scored


# =============================================================================
# MAIN SCORING FUNCTION
# =============================================================================

def score_strategies(results: list) -> list:
    """
    Score and rank a list of backtest results for a single token.

    Steps:
    1. Filter out results that don't meet minimum thresholds
    2. Calculate RELATIVE composite score (for within-token ranking)
    3. Calculate ABSOLUTE composite score (for cross-token tier assignment)
    4. Rank by relative score descending
    5. Apply timeframe tiebreaker
    6. Return ranked list — NO tier assignment (done globally in score_all_tokens)

    composite_score = relative score (for ordering within-token candidates)
    absolute_score  = absolute score (for cross-token tier assignment)
    """
    if not results:
        return []

    # Step 1 — Filter by minimum thresholds on validation metrics
    passing = []
    for r in results:
        val_metrics = r.get("val_metrics", {})
        if val_metrics and passes_minimum_thresholds(val_metrics):
            passing.append(r)

    if not passing:
        logger.debug("No strategies passed minimum thresholds")
        return []

    # Step 2 — Calculate relative composite scores (for within-token ranking)
    all_val_metrics = [r["val_metrics"] for r in passing]
    for r in passing:
        r["composite_score"] = calculate_composite_score_relative(
            r["val_metrics"], all_val_metrics
        )
        # Step 3 — Calculate absolute score (for cross-token comparison)
        r["absolute_score"] = calculate_composite_score_absolute(r["val_metrics"])

    # Step 4 — Rank by relative score descending (best strategy for this token first)
    ranked = sorted(passing, key=lambda x: x["composite_score"], reverse=True)

    # Step 5 — Apply timeframe tiebreaker
    ranked = apply_timeframe_tiebreaker(ranked)

    # Step 6 — Promote absolute score to composite_score for storage in DB
    # The DB and assigner use composite_score — we want absolute score stored
    # so that tier assignment and dashboard reflect real quality.
    for r in ranked:
        r["composite_score"] = r["absolute_score"]

    logger.info(
        f"Scored {len(results)} results → {len(passing)} passed thresholds → "
        f"{len(ranked)} ranked (tier assignment pending global pass)"
    )

    return ranked


# =============================================================================
# SCORE ALL TOKENS + GLOBAL TIER ASSIGNMENT
# =============================================================================

def score_all_tokens(all_results: dict) -> dict:
    """
    Score strategies for all tokens, then assign tiers globally.

    Two-pass process:
    Pass 1: Score and rank each token's strategies independently
    Pass 2: Assign tiers based on global absolute score distribution
    """
    scored = {}
    total  = len(all_results)

    # Pass 1 — Score all tokens
    for i, (symbol, results) in enumerate(all_results.items(), 1):
        logger.info(f"[{i}/{total}] Scoring {symbol} ({len(results)} results)...")
        scored[symbol] = score_strategies(results)
        best = scored[symbol][0] if scored[symbol] else None
        if best:
            vm = best["val_metrics"]
            logger.info(
                f"  Best: {best['timeframe']} | "
                f"Score: {best['composite_score']:.4f} | "
                f"WR: {vm['win_rate']:.1%} | "
                f"Exp: {vm['expectancy']:.4f}"
            )
        else:
            logger.warning(f"  No valid strategy found for {symbol}")

    # Pass 2 — Assign tiers globally across all tokens
    logger.info("Assigning tiers globally across all tokens...")
    scored = assign_tiers_globally(scored)

    # Log final results
    for symbol, results in scored.items():
        if results:
            best = results[0]
            logger.info(
                f"  {symbol.replace('/USDT:USDT', '')} → "
                f"{best['timeframe']} | Tier: {best['assigned_tier']} | "
                f"Score: {best['composite_score']:.4f}"
            )

    return scored


# =============================================================================
# REPORTING & ANALYSIS
# =============================================================================

def get_scoring_summary(scored_results: dict) -> pd.DataFrame:
    """Generate a summary DataFrame of best strategies per token."""
    rows = []

    for symbol, results in scored_results.items():
        if not results:
            rows.append({
                "symbol":        symbol,
                "timeframe":     "—",
                "tier":          "—",
                "score":         0,
                "win_rate":      0,
                "expectancy":    0,
                "profit_factor": 0,
                "max_drawdown":  0,
                "sharpe":        0,
                "indicators":    "—",
                "status":        "NO STRATEGY",
            })
            continue

        best = results[0]
        vm   = best["val_metrics"]

        rows.append({
            "symbol":        symbol.replace("/USDT:USDT", ""),
            "timeframe":     best["timeframe"],
            "tier":          best.get("assigned_tier", "—"),
            "score":         round(best["composite_score"], 4),
            "win_rate":      round(vm["win_rate"] * 100, 1),
            "expectancy":    round(vm["expectancy"], 4),
            "profit_factor": round(vm["profit_factor"], 2),
            "max_drawdown":  round(vm["max_drawdown"] * 100, 1),
            "sharpe":        round(vm["sharpe_ratio"], 2),
            "indicators":    "+".join(
                [i for i in best["indicators"] if i not in ["ema", "macro_ref"]]
            ),
            "status":        "OK",
        })

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("score", ascending=False).reset_index(drop=True)

    return df


def print_scoring_summary(scored_results: dict):
    """Print a formatted scoring summary to the log."""
    df = get_scoring_summary(scored_results)
    if df.empty:
        logger.info("No strategies scored.")
        return

    logger.info("\n" + "=" * 80)
    logger.info("APEX STRATEGY SCORING SUMMARY")
    logger.info("=" * 80)
    logger.info(f"\n{df.to_string(index=False)}")
    logger.info("=" * 80)

    ok_count   = len(df[df["status"] == "OK"])
    none_count = len(df[df["status"] == "NO STRATEGY"])
    tier1      = len(df[df["tier"] == "tier1"])
    tier2      = len(df[df["tier"] == "tier2"])
    tier3      = len(df[df["tier"] == "tier3"])

    logger.info(f"\nTokens with valid strategy: {ok_count}")
    logger.info(f"Tokens with no strategy:    {none_count}")
    logger.info(f"Tier 1 (High):              {tier1}")
    logger.info(f"Tier 2 (Medium):            {tier2}")
    logger.info(f"Tier 3 (Low):               {tier3}")
    logger.info("=" * 80)


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s"
    )

    from apex.data_fetcher import get_db_connection
    from apex.backtest_engine import run_backtest_for_token

    conn    = get_db_connection()
    symbols = ["BTC/USDT:USDT", "ETH/USDT:USDT", "DOGE/USDT:USDT"]

    all_results = {}
    for sym in symbols:
        all_results[sym] = run_backtest_for_token(conn, sym)

    scored = score_all_tokens(all_results)
    print_scoring_summary(scored)

    conn.close()

# __APEX_LOGGER_V1__
