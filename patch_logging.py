#!/usr/bin/env python3
"""
APEX Logging Integration Patch v1.0
=====================================
Surgically adds structured JSONL logging to all 12 bot modules.

Usage:
    cd /home/opc/tradingbot
    python3 patch_logging.py

Safe to run multiple times — already-patched files are skipped.
All files are backed up before modification.
Backups stored in: /home/opc/tradingbot/backups/log_patch_TIMESTAMP/
"""

import shutil, sys
from pathlib import Path
from datetime import datetime

BASE   = Path("/home/opc/tradingbot")
BDIR   = BASE / f"backups/log_patch_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
MARKER = "# __APEX_LOGGER_V1__"
ok=[];skip=[];fail=[]

def bak(f):
    dst = BDIR / f.relative_to(BASE)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(f, dst)

def patch(rel, replacements, append=None):
    f = BASE / rel
    if not f.exists():
        print(f"  ✗ MISSING: {rel}")
        fail.append(rel)
        return
    txt = f.read_text()
    if MARKER in txt:
        print(f"  ⏭  SKIP (already patched): {rel}")
        skip.append(rel)
        return
    bak(f)
    warns = []
    for old, new in replacements:
        if old not in txt:
            warns.append(f"    pattern not found: {old[:80]!r}")
        else:
            txt = txt.replace(old, new, 1)
    if append:
        txt = txt.rstrip() + "\n\n" + append.strip() + "\n"
    txt += f"\n{MARKER}\n"
    f.write_text(txt)
    status = "  ⚠  PARTIAL" if warns else "  ✓ OK"
    print(f"{status}: {rel}")
    for w in warns:
        print(w)
    ok.append(rel)


print("=" * 60)
print("APEX Logging Integration Patch v1.0")
print(f"Backup dir: {BDIR}")
print("=" * 60)


# ═══════════════════════════════════════════════════════════════════════
# 1. bot/config.py — Append logger init + get_config_dict()
# ═══════════════════════════════════════════════════════════════════════
print("\n[1/12] bot/config.py")
patch("bot/config.py", [], append="""
# ── APEX Event Logger ────────────────────────────────────────────────────────
# Initialized here so every module can do: from bot.config import apex_logger
from bot.logger import APEXLogger as _APEXLogger
APEX_LOG_DIR = str(BASE_DIR.parent / "logs" / "apex_events")
apex_logger  = _APEXLogger(APEX_LOG_DIR)

def get_config_dict() -> dict:
    \"\"\"Return current config as a snapshot dict for structured logging.\"\"\"
    return {
        "PAPER_TRADING":       PAPER_TRADING,
        "INITIAL_CAPITAL":     INITIAL_CAPITAL,
        "RESERVE_PCT":         RESERVE_PCT,
        "MAX_DEPLOYED_PCT":    MAX_DEPLOYED_PCT,
        "CAPITAL_PER_SLOT":    CAPITAL_PER_SLOT,
        "MAX_LEVERAGE":        MAX_LEVERAGE,
        "DRAWDOWN":            DRAWDOWN,
        "GO_LIVE_CRITERIA":    GO_LIVE_CRITERIA,
        "PERFORMANCE_MONITOR": PERFORMANCE_MONITOR,
        "SCORING_MINIMUMS":    SCORING_MINIMUMS,
        "BTC_FILTER_ENABLED":  BTC_FILTER.get("enabled", True),
        "FILTERS_ENABLED": {
            k: v.get("enabled", True)
            for k, v in FILTERS.items()
            if isinstance(v, dict)
        },
    }
""")


# ═══════════════════════════════════════════════════════════════════════
# 2. bot/main.py
# ═══════════════════════════════════════════════════════════════════════
print("\n[2/12] bot/main.py")
patch("bot/main.py", [

    # Change 1 — add apex_logger and get_config_dict to imports
    (
        "from bot.config import PAPER_TRADING, INITIAL_CAPITAL, LOGS, DB",
        "from bot.config import PAPER_TRADING, INITIAL_CAPITAL, LOGS, DB, apex_logger, get_config_dict"
    ),

    # Change 2 — log bot_start in startup() before send_startup_alert
    (
        "    send_startup_alert(PAPER_TRADING)\n    logger.info(\"Startup complete — entering main loop\")",
        """    apex_logger.bot_start(
        version         = "3.0",
        mode            = "PAPER" if PAPER_TRADING else "LIVE",
        initial_capital = INITIAL_CAPITAL,
        config_snapshot = get_config_dict(),
    )
    send_startup_alert(PAPER_TRADING)
    logger.info("Startup complete — entering main loop")"""
    ),

    # Change 3 — log cycle_start after OHLCV data is confirmed good
    (
        "        if not ohlcv_data:\n            logger.error(\"No OHLCV data — skipping cycle\")\n            return",
        """        if not ohlcv_data:
            logger.error("No OHLCV data — skipping cycle")
            return

        # Log cycle heartbeat with full market context
        _btc_d = cycle_data.get("btc_trend", {})
        _fg_d  = cycle_data.get("fear_greed", {})
        apex_logger.cycle_start(
            cycle_number         = state.cycle_count,
            open_trades          = len(get_open_trades(trades_conn)),
            capital_deployed_pct = 0.0,
            fg_index             = _fg_d.get("value", 50),
            fg_label             = _fg_d.get("label", "Unknown"),
            btc_trend            = _btc_d.get("direction", "neutral"),
            btc_price            = 0.0,
            total_equity         = get_capital(trades_conn),
        )"""
    ),

    # Change 4 — log bot_error in except block
    (
        "        logger.error(f\"Cycle #{state.cycle_count} FAILED after {elapsed:.1f}s: {e}\", exc_info=True)\n        send_telegram(",
        """        logger.error(f"Cycle #{state.cycle_count} FAILED after {elapsed:.1f}s: {e}", exc_info=True)
        apex_logger.bot_error(str(e), {"cycle_number": state.cycle_count})
        send_telegram("""
    ),

    # Change 5 — log bot_stop in _cleanup()
    (
        "    logger.info(\"Shutdown complete.\")",
        """    apex_logger.bot_stop("shutdown", 0.0, get_bot_state().cycle_count, 0)
    logger.info("Shutdown complete.")"""
    ),
])


# ═══════════════════════════════════════════════════════════════════════
# 3. bot/signal_engine.py — Log every scan result in generate_signals()
# ═══════════════════════════════════════════════════════════════════════
print("\n[3/12] bot/signal_engine.py")
patch("bot/signal_engine.py", [
    (
        """        try:
            signal = generate_signal_for_token(
                strategy         = strategy,
                df_1h            = df_1h,
                df_4h            = df_4h,
                df_1d            = df_1d,
                cycle_data       = cycle_data,
                open_trades      = open_trades,
                price_history    = price_history,
                cooldown_tracker = cooldown_tracker,
            )
            if signal:
                signals.append(signal)

        except Exception as e:
            logger.error(f"Signal error {symbol}: {e}", exc_info=True)""",

        """        try:
            signal = generate_signal_for_token(
                strategy         = strategy,
                df_1h            = df_1h,
                df_4h            = df_4h,
                df_1d            = df_1d,
                cycle_data       = cycle_data,
                open_trades      = open_trades,
                price_history    = price_history,
                cooldown_tracker = cooldown_tracker,
            )
            if signal:
                signals.append(signal)
                try:
                    from bot.config import apex_logger
                    _fg  = cycle_data.get("fear_greed", {})
                    _btc = cycle_data.get("btc_trend", {})
                    apex_logger.signal_scan_complete(
                        token      = symbol.replace("/USDT:USDT", ""),
                        timeframe  = signal["timeframe"],
                        strategy   = strategy.get("strategy_type", strategy.get("tier", "unknown")),
                        tier       = signal["tier"],
                        result     = "entered",
                        reason     = "all_conditions_met",
                        direction  = signal["direction"],
                        indicators = {
                            "indicators_agreeing": signal.get("confluence_count", 0),
                            "rrr":                 signal.get("rrr", 0),
                        },
                        filters_result = {
                            "passed":    True,
                            "btc_trend": _btc.get("direction", "?"),
                            "fg_index":  _fg.get("value", 50),
                        },
                        market = {
                            "price":         signal["entry"],
                            "atr":           signal.get("atr", 0),
                            "potential_rrr": signal.get("rrr", 0),
                        },
                    )
                except Exception:
                    pass
            else:
                try:
                    from bot.config import apex_logger
                    _fg  = cycle_data.get("fear_greed", {})
                    _btc = cycle_data.get("btc_trend", {})
                    apex_logger.signal_scan_complete(
                        token      = symbol.replace("/USDT:USDT", ""),
                        timeframe  = strategy.get("timeframe", "1h"),
                        strategy   = strategy.get("strategy_type", strategy.get("tier", "unknown")),
                        tier       = strategy["tier"],
                        result     = "no_signal",
                        reason     = "filtered_or_no_confluence",
                        direction  = None,
                        indicators = {},
                        filters_result = {
                            "passed":    False,
                            "btc_trend": _btc.get("direction", "?"),
                            "fg_index":  _fg.get("value", 50),
                        },
                        market = {},
                    )
                except Exception:
                    pass

        except Exception as e:
            logger.error(f"Signal error {symbol}: {e}", exc_info=True)"""
    ),
])


# ═══════════════════════════════════════════════════════════════════════
# 4. bot/filters.py — Log first blocking filter in run_all_filters()
# ═══════════════════════════════════════════════════════════════════════
print("\n[4/12] bot/filters.py")
patch("bot/filters.py", [
    (
        "            if not result.passed:\n                failures.append(filter_name)",
        """            if not result.passed:
                failures.append(filter_name)
                if len(failures) == 1:  # Log the first blocking filter per scan
                    try:
                        from bot.config import apex_logger
                        apex_logger.filter_rejection(
                            token        = symbol,
                            filter_name  = filter_name,
                            side         = direction,
                            value        = result.reason,
                            threshold    = None,
                            full_context = {
                                "tier":             tier,
                                "btc_trend":        btc_trend.get("direction","?") if isinstance(btc_trend,dict) else str(btc_trend),
                                "fg_index":         fear_greed_value,
                                "funding_rate":     funding_rate,
                                "confluence_count": confluence_count,
                                "daily_volume_usd": daily_volume_usd,
                            },
                        )
                    except Exception:
                        pass"""
    ),
])


# ═══════════════════════════════════════════════════════════════════════
# 5. bot/trade_manager.py — Log leg 1 entry and trade exit
# ═══════════════════════════════════════════════════════════════════════
print("\n[5/12] bot/trade_manager.py")
patch("bot/trade_manager.py", [

    # Log leg 1 entry (after logger.info in open_trade)
    (
        """    logger.info(
        f"Trade opened: {symbol.replace('/USDT:USDT','')} "
        f"{direction.upper()} | {tier} | {timeframe} | "
        f"Entry: {fill_price_leg1:.4f} | "
        f"SL: {stop_loss:.4f} | TP: {take_profit:.4f} | "
        f"Qty: {qty_leg1} (Leg 1 of 2) | "
        f"ID: {trade_id}"
    )

    return trade_id""",

        """    logger.info(
        f"Trade opened: {symbol.replace('/USDT:USDT','')} "
        f"{direction.upper()} | {tier} | {timeframe} | "
        f"Entry: {fill_price_leg1:.4f} | "
        f"SL: {stop_loss:.4f} | TP: {take_profit:.4f} | "
        f"Qty: {qty_leg1} (Leg 1 of 2) | "
        f"ID: {trade_id}"
    )

    try:
        from bot.config import apex_logger
        _tid = f"{symbol.replace('/USDT:USDT','')}_{now[:10].replace('-','')}_{now[11:16].replace(':','')}"
        apex_logger.trade_entry_leg(
            trade_id         = _tid,
            token            = symbol,
            side             = direction,
            leg              = 1,
            entry_price      = fill_price_leg1,
            size_usd         = round(position_usdt * ENTRY["leg1_pct"], 2),
            sl_price         = stop_loss,
            tp_price         = take_profit,
            rrr              = rrr,
            sl_method        = "atr_and_structure",
            strategy         = tier,
            tier             = tier,
            timeframe        = timeframe,
            decision_context = {
                "signal_score":  signal_score,
                "confluence":    confluence,
                "db_trade_id":   trade_id,
                "leverage":      leverage,
            },
        )
    except Exception:
        pass

    return trade_id"""
    ),

    # Log trade exit (after logger.info in close_trade)
    (
        """    logger.info(
        f"Trade closed: {symbol.replace('/USDT:USDT','')} "
        f"{direction.upper()} | "
        f"Exit: {fill_exit:.4f} | "
        f"PnL: {total_pnl:+.2f} USDT ({pnl_pct:+.2f}%) | "
        f"Reason: {reason}"
    )

    trade["exit_price"]  = fill_exit""",

        """    logger.info(
        f"Trade closed: {symbol.replace('/USDT:USDT','')} "
        f"{direction.upper()} | "
        f"Exit: {fill_exit:.4f} | "
        f"PnL: {total_pnl:+.2f} USDT ({pnl_pct:+.2f}%) | "
        f"Reason: {reason}"
    )

    try:
        from bot.config import apex_logger
        apex_logger.trade_exit(
            trade_id         = str(trade["id"]),
            token            = symbol,
            side             = direction,
            exit_price       = fill_exit,
            entry_price      = avg_entry,
            exit_reason      = reason,
            pnl_usd          = round(total_pnl, 4),
            pnl_pct          = round(pnl_pct, 4),
            duration_candles = trade.get("candles_open", 0),
            strategy         = trade.get("tier", "unknown"),
            tier             = trade.get("tier", "unknown"),
            timeframe        = trade.get("timeframe", "1h"),
            market_at_exit   = {},
        )
    except Exception:
        pass

    trade["exit_price"]  = fill_exit"""
    ),
])


# ═══════════════════════════════════════════════════════════════════════
# 6. bot/capital_manager.py — Log queue add, drop, promote
# ═══════════════════════════════════════════════════════════════════════
print("\n[6/12] bot/capital_manager.py")
patch("bot/capital_manager.py", [

    # Log signal queued in SignalQueue.add()
    (
        """        logger.info(
            f"Signal queued: {signal['symbol'].replace('/USDT:USDT','')} "
            f"{signal['direction']} | Score: {signal['signal_score']:.4f} | "
            f"Queue size: {len(self.queue)}"
        )""",

        """        logger.info(
            f"Signal queued: {signal['symbol'].replace('/USDT:USDT','')} "
            f"{signal['direction']} | Score: {signal['signal_score']:.4f} | "
            f"Queue size: {len(self.queue)}"
        )
        try:
            from bot.config import apex_logger
            apex_logger.signal_queued(
                token          = signal["symbol"],
                tier           = signal["tier"],
                score          = signal["signal_score"],
                queue_position = len(self.queue),
                queue_reason   = "no_slot_available",
            )
        except Exception:
            pass"""
    ),

    # Log stale drop by age
    (
        "                logger.debug(f\"Dropping stale signal: {symbol} (age: {age_mins:.0f} mins)\")\n                continue",
        """                logger.debug(f"Dropping stale signal: {symbol} (age: {age_mins:.0f} mins)")
                try:
                    from bot.config import apex_logger
                    apex_logger.signal_queue_dropped(token=symbol, reason=f"stale_{age_mins:.0f}mins")
                except Exception: pass
                continue"""
    ),

    # Log stale drop by missing data
    (
        "                logger.debug(f\"Dropping stale signal: {symbol} — no current data\")\n                continue",
        """                logger.debug(f"Dropping stale signal: {symbol} — no current data")
                try:
                    from bot.config import apex_logger
                    apex_logger.signal_queue_dropped(token=symbol, reason="no_current_ohlcv_data")
                except Exception: pass
                continue"""
    ),

    # Log queue promotion in allocate_signals()
    (
        """            logger.info(
                f"Queued signal executed: {symbol.replace('/USDT:USDT','')} "
                f"{queued['direction']} | {tier}"
            )""",
        """            logger.info(
                f"Queued signal executed: {symbol.replace('/USDT:USDT','')} "
                f"{queued['direction']} | {tier}"
            )
            try:
                from bot.config import apex_logger
                apex_logger.signal_queue_promoted(token=symbol, tier=tier, wait_cycles=1)
            except Exception:
                pass"""
    ),
])


# ═══════════════════════════════════════════════════════════════════════
# 7. bot/risk_manager.py — Log drawdown events at each threshold
# ═══════════════════════════════════════════════════════════════════════
print("\n[7/12] bot/risk_manager.py")
patch("bot/risk_manager.py", [

    # STOP — after set_stopped()
    (
        "            risk_state.set_stopped()\n            _close_all_trades(conn, open_trades)",
        """            risk_state.set_stopped()
            try:
                from bot.config import apex_logger
                apex_logger.drawdown_event(
                    "stop_50", dd_pct, "stopped_all",
                    risk_state.peak_capital, capital, len(open_trades)
                )
            except Exception: pass
            _close_all_trades(conn, open_trades)"""
    ),

    # PAUSE — after set_paused()
    (
        "            risk_state.set_paused()\n            _send_circuit_breaker_alert(\"pause\", dd_pct, capital)",
        """            risk_state.set_paused()
            try:
                from bot.config import apex_logger
                apex_logger.drawdown_event(
                    "pause_35", dd_pct, "paused_new_entries",
                    risk_state.peak_capital, capital, len(open_trades)
                )
            except Exception: pass
            _send_circuit_breaker_alert("pause", dd_pct, capital)"""
    ),

    # ALERT — after alert_sent = True
    (
        "            risk_state.alert_sent = True\n            _send_circuit_breaker_alert(\"alert\", dd_pct, capital)",
        """            risk_state.alert_sent = True
            try:
                from bot.config import apex_logger
                apex_logger.drawdown_event(
                    "alert_20", dd_pct, "telegram_alert",
                    risk_state.peak_capital, capital, len(open_trades)
                )
            except Exception: pass
            _send_circuit_breaker_alert("alert", dd_pct, capital)"""
    ),
])


# ═══════════════════════════════════════════════════════════════════════
# 8. bot/performance_monitor.py — Log pause and resume events
# ═══════════════════════════════════════════════════════════════════════
print("\n[8/12] bot/performance_monitor.py")
patch("bot/performance_monitor.py", [

    # Log performance pause
    (
        "        pause_token_in_apex(symbol, reason)\n        send_pause_alert(symbol, reason, metrics)",
        """        pause_token_in_apex(symbol, reason)
        try:
            from bot.config import apex_logger
            apex_logger.performance_pause(
                token                  = symbol,
                reason                 = reason,
                last_20_win_rate       = metrics["win_rate"],
                last_20_expectancy     = metrics["expectancy"],
                last_20_trades_summary = [],
            )
        except Exception: pass
        send_pause_alert(symbol, reason, metrics)"""
    ),
])


# ═══════════════════════════════════════════════════════════════════════
# 9. apex/backtest_engine.py — Log best result per token
# ═══════════════════════════════════════════════════════════════════════
print("\n[9/12] apex/backtest_engine.py")
patch("apex/backtest_engine.py", [
    (
        """        try:
            all_results[symbol] = run_backtest_for_token(conn, symbol)
        except Exception as e:
            logger.error(f"Failed {symbol}: {e}")
            all_results[symbol] = []""",

        """        try:
            results = run_backtest_for_token(conn, symbol)
            all_results[symbol] = results
            if results:
                try:
                    from bot.config import apex_logger
                    best = results[0]; vm = best["val_metrics"]
                    apex_logger.backtest_result(
                        token          = symbol,
                        timeframe      = best["timeframe"],
                        strategy_name  = best.get("strategy_type", "unknown"),
                        win_rate       = vm["win_rate"],
                        expectancy     = vm["expectancy"],
                        profit_factor  = vm["profit_factor"],
                        sharpe         = vm["sharpe_ratio"],
                        max_drawdown   = vm["max_drawdown"],
                        total_trades   = vm["n_trades"],
                        passed_filter  = True,
                    )
                except Exception: pass
        except Exception as e:
            logger.error(f"Failed {symbol}: {e}")
            all_results[symbol] = []"""
    ),
])


# ═══════════════════════════════════════════════════════════════════════
# 10. apex/strategy_assigner.py — Log every strategy assignment
# ═══════════════════════════════════════════════════════════════════════
print("\n[10/12] apex/strategy_assigner.py")
patch("apex/strategy_assigner.py", [
    (
        """    conn.commit()

    logger.info(
        f"Strategy assigned: {symbol.replace('/USDT:USDT','')} | \"""",

        """    conn.commit()

    try:
        from bot.config import apex_logger
        apex_logger.strategy_assigned(
            token              = symbol,
            strategy           = best.get("strategy_type", best.get("timeframe", "unknown")),
            timeframe          = best["timeframe"],
            tier               = best.get("assigned_tier", best.get("tier", "unknown")),
            metrics            = vm,
            assignment_reason  = source,
        )
    except Exception: pass

    logger.info(
        f"Strategy assigned: {symbol.replace('/USDT:USDT','')} | \"""",
    ),
])


# ═══════════════════════════════════════════════════════════════════════
# 11. apex/universe_manager.py — Log token adds, removes, summary
# ═══════════════════════════════════════════════════════════════════════
print("\n[11/12] apex/universe_manager.py")
patch("apex/universe_manager.py", [

    # Log each dropped token
    (
        "            mark_token_no_new_entries(conn, symbol)\n            # Note: existing trades on this token continue naturally",
        """            mark_token_no_new_entries(conn, symbol)
            try:
                from bot.config import apex_logger
                apex_logger.universe_token_removed(token=symbol, reason="dropped_out_of_top100")
            except Exception: pass
            # Note: existing trades on this token continue naturally"""
    ),

    # Log refresh summary and each added token
    (
        "    # Send Telegram alert\n    send_universe_alert(added_symbols, dropped_symbols)",
        """    # Send Telegram alert
    send_universe_alert(added_symbols, dropped_symbols)
    try:
        from bot.config import apex_logger
        apex_logger.universe_refresh_summary(
            tokens_added       = added_symbols,
            tokens_removed     = dropped_symbols,
            total_universe_size= sum(1 for v in after.values() if v["active"]),
            refresh_type       = "weekly",
        )
        for _sym in added_symbols:
            apex_logger.universe_token_added(token=_sym, market_cap_rank=0, daily_volume_usd=0)
    except Exception: pass"""
    ),
])


# ═══════════════════════════════════════════════════════════════════════
# 12. apex/rebalancer.py — Log rebalance start and complete
# ═══════════════════════════════════════════════════════════════════════
print("\n[12/12] apex/rebalancer.py")
patch("apex/rebalancer.py", [

    # Log rebalance start
    (
        "        send_rebalance_alert(\"monthly_start\", {\"total\": total})",
        """        send_rebalance_alert("monthly_start", {"total": total})
        try:
            from bot.config import apex_logger
            apex_logger.rebalance_event("start", "monthly", token_count=total)
        except Exception: pass"""
    ),

    # Log rebalance complete
    (
        """        send_rebalance_alert("monthly_complete", {
            "assigned":     len(summary["assigned"]),
            "unassigned":   len(summary["unassigned"]),
            "duration_mins": duration_min,
        })""",

        """        send_rebalance_alert("monthly_complete", {
            "assigned":     len(summary["assigned"]),
            "unassigned":   len(summary["unassigned"]),
            "duration_mins": duration_min,
        })
        try:
            from bot.config import apex_logger
            apex_logger.rebalance_event("complete", "monthly",
                duration_seconds = duration_sec,
                tokens_changed   = len(summary["assigned"]),
                tokens_unchanged = len(summary["unassigned"]),
            )
        except Exception: pass"""
    ),
])


# ═══════════════════════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print(f"Done. Patched: {len(ok)} | Skipped: {len(skip)} | Failed: {len(fail)}")
if ok:    print(f"  Patched : {ok}")
if skip:  print(f"  Skipped : {skip}")
if fail:  print(f"  Failed  : {fail}")
print(f"  Backups : {BDIR}")
print("=" * 60)

if fail:
    print("\nWARNING: Some files were missing. Check the list above.")
    sys.exit(1)

print("\nNext steps:")
print("  1. Verify backups exist in:", BDIR)
print("  2. Stop the bot: pkill -f 'python3 -m bot.main'")
print("  3. Start it: nohup python3 -m bot.main > logs/bot.log 2>&1 &")
print("  4. Check logs: tail -f logs/bot.log")
print("  5. Check apex events: ls logs/apex_events/")
