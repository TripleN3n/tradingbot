# =============================================================================
# APEX — Adaptive Per-token Execution Strategy Engine
# apex/strategy_scorer.py — Strategy Scoring & Ranking
# =============================================================================
# RESPONSIBILITY:
# Takes raw backtest results from backtest_engine.py and produces a
# ranked list of strategies per token per timeframe.
# Applies the 5-metric composite scoring formula.
# Enforces the timeframe tiebreaker rule.
#
# WHAT THIS FILE DOES:
# - Receives list of backtest results per token
# - Calculates composite score using weighted 5-metric formula
# - Filters out strategies that don't meet minimum thresholds
# - Ranks strategies by composite score
# - Applies timeframe tiebreaker (within 5% → prefer higher timeframe)
# - Assigns tiers GLOBALLY across all tokens (not per-token)
# - Returns best strategy per token ready for strategy_assigner.py
#
# TIER ASSIGNMENT — CRITICAL DESIGN NOTE:
# Tiers are assigned AFTER scoring ALL tokens, not per-token.
# A token's tier reflects how its best strategy ranks against ALL other tokens.
# Tier 1 = top 25% of all tokens by composite score
# Tier 2 = 50th–75th percentile
# Tier 3 = 25th–50th percentile
# Below 25th percentile = excluded from trading
# This ensures tier2 is always populated and tiers are meaningful.
# =============================================================================

import pandas as pd
import numpy as np
import logging
import sys
from pathlib import Path
from typing import Optional

sys.path.append(str(Path(__file__).resolve().parent.parent))
from bot.config import (
    SCORING_WEIGHTS, SCORING_MINIMUMS, TIERS,
    TIMEFRAME_TIEBREAKER_PCT, TIMEFRAME_PRIORITY, LOGS,
)

# =============================================================================
# LOGGING
# =============================================================================

logger = logging.getLogger(__name__)


# =============================================================================
# COMPOSITE SCORE CALCULATION
# =============================================================================

def normalise(value: float, min_val: float, max_val: float) -> float:
    """
    Normalise a value to 0-1 range.
    Returns 0.5 if range is zero.
    """
    if max_val == min_val:
        return 0.5
    return max(0.0, min(1.0, (value - min_val) / (max_val - min_val)))


def calculate_composite_score(metrics: dict, all_metrics: list) -> float:
    """
    Calculate composite score for a strategy using weighted 5-metric formula.
    All metrics are normalised relative to the full result set before weighting.
    This ensures fair comparison across different scales.

    Score = weighted sum of normalised:
    - Expectancy    (weight: 0.35) — higher is better
    - Win Rate      (weight: 0.25) — higher is better
    - Max Drawdown  (weight: 0.15) — lower is better (inverted)
    - Profit Factor (weight: 0.15) — higher is better
    - Sharpe Ratio  (weight: 0.10) — higher is better
    """
    expectancies    = [m["expectancy"]    for m in all_metrics]
    win_rates       = [m["win_rate"]      for m in all_metrics]
    drawdowns       = [m["max_drawdown"]  for m in all_metrics]
    profit_factors  = [m["profit_factor"] for m in all_metrics]
    sharpes         = [m["sharpe_ratio"]  for m in all_metrics]

    norm_exp  = normalise(metrics["expectancy"],    min(expectancies),   max(expectancies))
    norm_wr   = normalise(metrics["win_rate"],      min(win_rates),      max(win_rates))
    norm_dd   = normalise(metrics["max_drawdown"],  min(drawdowns),      max(drawdowns))
    norm_pf   = normalise(metrics["profit_factor"], min(profit_factors), max(profit_factors))
    norm_sh   = normalise(metrics["sharpe_ratio"],  min(sharpes),        max(sharpes))

    norm_dd_inv = 1.0 - norm_dd

    score = (
        SCORING_WEIGHTS["expectancy"]    * norm_exp    +
        SCORING_WEIGHTS["win_rate"]      * norm_wr     +
        SCORING_WEIGHTS["max_drawdown"]  * norm_dd_inv +
        SCORING_WEIGHTS["profit_factor"] * norm_pf     +
        SCORING_WEIGHTS["sharpe_ratio"]  * norm_sh
    )

    return round(score, 6)


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
    - If top 2 strategies have scores within TIMEFRAME_TIEBREAKER_PCT of each other
    - Prefer the one with higher timeframe priority
    - If gap > threshold, keep highest score regardless

    TIMEFRAME_PRIORITY = ["1h", "4h", "1d"] (1d has highest priority index)
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
                f"Tiebreaker applied: {second['timeframe']} preferred over "
                f"{best['timeframe']} (gap: {gap:.2%})"
            )
            ranked[0], ranked[1] = ranked[1], ranked[0]

    return ranked


# =============================================================================
# GLOBAL TIER ASSIGNMENT
# =============================================================================

def assign_tiers_globally(scored: dict) -> dict:
    """
    Assign tiers based on each token's best composite score relative to ALL tokens.

    This must be called after ALL tokens have been scored — not per-token.
    Each token competes against every other token for tier placement.

    Tier 1 (High):   top 25% of all token best scores
    Tier 2 (Medium): 50th–75th percentile
    Tier 3 (Low):    25th–50th percentile
    Below 25th:      excluded from trading (no strategy assigned)
    """
    # Collect best score per token (only tokens with at least one valid strategy)
    best_scores = {
        symbol: results[0]["composite_score"]
        for symbol, results in scored.items()
        if results
    }

    if not best_scores:
        logger.warning("No tokens with valid strategies — cannot assign tiers")
        return scored

    all_score_values = list(best_scores.values())
    p25, p50, p75    = np.percentile(all_score_values, [25, 50, 75])

    tier_counts = {"tier1": 0, "tier2": 0, "tier3": 0, "excluded": 0}

    for symbol, results in scored.items():
        if not results:
            continue

        score = results[0]["composite_score"]

        if score >= p75:
            tier = "tier1"
        elif score >= p50:
            tier = "tier2"
        elif score >= p25:
            tier = "tier3"
        else:
            tier = None  # Below 25th percentile — exclude from trading

        if tier is None:
            scored[symbol] = []  # Remove from active trading
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
# MAIN SCORING FUNCTION (per token — NO tier assignment here)
# =============================================================================

def score_strategies(results: list) -> list:
    """
    Score and rank a list of backtest results for a single token.

    Steps:
    1. Filter out results that don't meet minimum thresholds
    2. Calculate composite score for each result
    3. Rank by composite score descending
    4. Apply timeframe tiebreaker
    5. Return ranked list — NO tier assignment (done globally in score_all_tokens)

    Input: list of result dicts from backtest_engine.run_backtest_for_token()
    Output: ranked list of result dicts with composite_score added (no tier yet)
    """
    if not results:
        return []

    # Step 1 — Filter by minimum thresholds (validation metrics)
    passing = []
    for r in results:
        val_metrics = r.get("val_metrics", {})
        if val_metrics and passes_minimum_thresholds(val_metrics):
            passing.append(r)

    if not passing:
        logger.debug("No strategies passed minimum thresholds")
        return []

    # Step 2 — Calculate composite scores
    all_val_metrics = [r["val_metrics"] for r in passing]
    for r in passing:
        r["composite_score"] = calculate_composite_score(r["val_metrics"], all_val_metrics)

    # Step 3 — Rank by composite score descending
    ranked = sorted(passing, key=lambda x: x["composite_score"], reverse=True)

    # Step 4 — Apply timeframe tiebreaker
    ranked = apply_timeframe_tiebreaker(ranked)

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
    Pass 2: Assign tiers based on global best-score distribution across all tokens

    Input:  dict of symbol -> list of backtest results
    Output: dict of symbol -> ranked list with composite_score and assigned_tier
    """
    scored = {}
    total  = len(all_results)

    # Pass 1 — Score all tokens
    for i, (symbol, results) in enumerate(all_results.items(), 1):
        logger.info(f"[{i}/{total}] Scoring {symbol} ({len(results)} results)...")
        scored[symbol] = score_strategies(results)
        best = scored[symbol][0] if scored[symbol] else None
        if best:
            logger.info(
                f"  Best: {best['timeframe']} | "
                f"Score: {best['composite_score']:.4f} | "
                f"WR: {best['val_metrics']['win_rate']:.1%} | "
                f"Exp: {best['val_metrics']['expectancy']:.4f}"
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
                f"  {symbol.replace('/USDT:USDT','')} → "
                f"{best['timeframe']} | Tier: {best['assigned_tier']} | "
                f"Score: {best['composite_score']:.4f}"
            )

    return scored


# =============================================================================
# REPORTING & ANALYSIS
# =============================================================================

def get_scoring_summary(scored_results: dict) -> pd.DataFrame:
    """
    Generate a summary DataFrame of best strategies per token.
    Used for logging, dashboard display, and debugging.
    """
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
            "indicators":    "+".join([i for i in best["indicators"] if i not in ["ema", "macro_ref"]]),
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
# ENTRY POINT — Run directly to test scoring on existing backtest results
# =============================================================================

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s"
    )

    from apex.data_fetcher import get_db_connection
    from apex.backtest_engine import run_backtest_for_token

    conn    = get_db_connection()
    symbols = ["BTC/USDT:USDT", "ETH/USDT:USDT"]

    all_results = {}
    for sym in symbols:
        all_results[sym] = run_backtest_for_token(conn, sym)

    scored = score_all_tokens(all_results)
    print_scoring_summary(scored)

    conn.close()
