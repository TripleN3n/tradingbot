# =============================================================================
# APEX — Adaptive Per-token Execution Strategy Engine
# bot/capital_manager.py — Capital & Slot Manager
# =============================================================================
# RESPONSIBILITY:
# Manages capital allocation, trade slot tracking, and signal queue.
# Decides which signals get executed and in what order based on
# tier priority, available slots, and signal scores.
#
# WHAT THIS FILE DOES:
# - Tracks open trade slots per tier
# - Enforces max slots per tier (Tier 1: 4, Tier 2: 3, Tier 3: 2)
# - Enforces 95% max capital deployment, 5% hard reserve
# - Manages signal queue — highest score gets next free slot
# - Drops stale signals from queue when conditions no longer valid
# - Calculates position size per trade (10% of initial capital)
# - Applies session-based size multiplier from filters
# - Calculates leverage (1x or 2x max)
#
# WHAT THIS FILE DOES NOT DO:
# - Does not generate signals (that's signal_engine.py)
# - Does not execute trades (that's trade_manager.py)
# - Does not manage risk/drawdown (that's risk_manager.py)
# =============================================================================

import logging
from datetime import datetime, timezone
from typing import Optional

from bot.config import (
    INITIAL_CAPITAL, CAPITAL_PER_SLOT, RESERVE_PCT,
    MAX_DEPLOYED_PCT, TIERS, MAX_LEVERAGE, MIN_LEVERAGE,
)

# =============================================================================
# LOGGING
# =============================================================================

logger = logging.getLogger(__name__)

# =============================================================================
# SLOT CONFIGURATION
# =============================================================================

MAX_SLOTS = {
    tier: config["max_slots"]
    for tier, config in TIERS.items()
}

TOTAL_MAX_SLOTS = sum(MAX_SLOTS.values())  # 4 + 3 + 2 = 9


# =============================================================================
# SLOT TRACKER
# =============================================================================

class SlotTracker:
    """
    Tracks open trade slots per tier.
    In-memory state, synced from database each cycle.
    """

    def __init__(self):
        self.open_slots = {tier: 0 for tier in TIERS}

    def sync_from_trades(self, open_trades: list):
        """Sync slot counts from current open trades list."""
        counts = {tier: 0 for tier in TIERS}
        for trade in open_trades:
            tier = trade.get("tier", "tier3")
            if tier in counts:
                counts[tier] += 1
        self.open_slots = counts
        logger.debug(f"Slot state: {self.open_slots}")

    def get_used_slots(self, tier: str) -> int:
        return self.open_slots.get(tier, 0)

    def get_available_slots(self, tier: str) -> int:
        used  = self.open_slots.get(tier, 0)
        max_s = MAX_SLOTS.get(tier, 0)
        return max(0, max_s - used)

    def get_total_open_slots(self) -> int:
        return sum(self.open_slots.values())

    def has_slot_available(self, tier: str) -> bool:
        return self.get_available_slots(tier) > 0

    def increment(self, tier: str):
        if tier in self.open_slots:
            self.open_slots[tier] += 1

    def decrement(self, tier: str):
        if tier in self.open_slots:
            self.open_slots[tier] = max(0, self.open_slots[tier] - 1)

    def summary(self) -> dict:
        return {
            tier: f"{self.open_slots[tier]}/{MAX_SLOTS[tier]}"
            for tier in TIERS
        }


# =============================================================================
# SIGNAL QUEUE
# =============================================================================

class SignalQueue:
    """
    Manages queued signals waiting for a free slot.
    Sorted by signal score — highest score gets next free slot.
    Stale signals are dropped automatically each cycle.
    """

    SIGNAL_TTL_MINUTES = {
        "1h": 60,
        "4h": 240,
        "1d": 1440,
    }

    def __init__(self):
        self.queue = []

    def add(self, signal: dict):
        """Add a signal to the queue and re-sort by score."""
        signal["queued_at"] = datetime.now(timezone.utc).isoformat()
        self.queue.append(signal)
        self.queue.sort(key=lambda x: x["signal_score"], reverse=True)
        logger.info(
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
            pass

    def drop_stale(self, current_ohlcv: dict):
        """Remove signals that have exceeded their TTL or have no current data."""
        now   = datetime.now(timezone.utc)
        valid = []

        for signal in self.queue:
            symbol    = signal["symbol"]
            timeframe = signal["timeframe"]
            queued_at = datetime.fromisoformat(signal["queued_at"])
            ttl_mins  = self.SIGNAL_TTL_MINUTES.get(timeframe, 60)
            age_mins  = (now - queued_at).total_seconds() / 60

            if age_mins > ttl_mins:
                logger.debug(f"Dropping stale signal: {symbol} (age: {age_mins:.0f} mins)")
                try:
                    from bot.config import apex_logger
                    apex_logger.signal_queue_dropped(token=symbol, reason=f"stale_{age_mins:.0f}mins")
                except Exception: pass
                continue

            if symbol not in current_ohlcv:
                logger.debug(f"Dropping stale signal: {symbol} — no current data")
                try:
                    from bot.config import apex_logger
                    apex_logger.signal_queue_dropped(token=symbol, reason="no_current_ohlcv_data")
                except Exception: pass
                continue

            valid.append(signal)

        dropped = len(self.queue) - len(valid)
        if dropped > 0:
            logger.debug(f"Dropped {dropped} stale signals from queue")

        self.queue = valid

    def get_next_for_tier(self, tier: str) -> Optional[dict]:
        """Get highest-scoring queued signal for a specific tier."""
        for signal in self.queue:
            if signal["tier"] == tier:
                return signal
        return None

    def remove(self, signal: dict):
        """Remove a specific signal from the queue."""
        self.queue = [s for s in self.queue if s is not signal]

    def size(self) -> int:
        return len(self.queue)

    def is_empty(self) -> bool:
        return len(self.queue) == 0


# =============================================================================
# CAPITAL CALCULATION
# =============================================================================

def calculate_position_size(size_multiplier: float = 1.0) -> float:
    """
    Calculate position size for a trade.

    Fixed 10% of INITIAL capital per slot — not compounding.
    Compounding is deliberately disabled during validation phase:
    - Prevents runaway losses
    - Backtested sizes match live sizes exactly
    - Clear evaluation of strategy performance

    Args:
        size_multiplier: Session filter multiplier (0.5 for Asian session)

    Returns position size in USDT.
    """
    base_size = INITIAL_CAPITAL * CAPITAL_PER_SLOT
    return round(base_size * size_multiplier, 2)


def calculate_leverage(sl_distance_pct: float) -> int:
    """
    Calculate appropriate leverage.
    Never exceeds MAX_LEVERAGE (2x).
    Never goes below MIN_LEVERAGE (1x).

    Args:
        sl_distance_pct: SL distance as a decimal fraction of entry (e.g. 0.05 = 5%)

    Returns leverage as integer.
    """
    if sl_distance_pct <= 0:
        return MIN_LEVERAGE

    target   = round(1 / sl_distance_pct)
    leverage = max(MIN_LEVERAGE, min(MAX_LEVERAGE, target))
    return int(leverage)


def get_capital_utilisation(capital: float, open_trades: list) -> dict:
    """Calculate current capital deployment status."""
    deployed   = sum(t.get("position_size_usdt", 0) for t in open_trades)
    reserve    = INITIAL_CAPITAL * RESERVE_PCT
    max_deploy = INITIAL_CAPITAL * MAX_DEPLOYED_PCT

    return {
        "capital":         capital,
        "deployed":        deployed,
        "available":       max(0, capital - deployed - reserve),
        "reserve":         reserve,
        "max_deployable":  max_deploy,
        "utilisation_pct": (deployed / capital * 100) if capital > 0 else 0,
        "at_capacity":     deployed >= max_deploy,
    }


def can_deploy_capital(capital: float, open_trades: list) -> bool:
    """Returns False if at 95% capital capacity."""
    return not get_capital_utilisation(capital, open_trades)["at_capacity"]


# =============================================================================
# SIGNAL ALLOCATION — CORE LOGIC
# =============================================================================

def allocate_signals(
    signals: list,
    slot_tracker: SlotTracker,
    signal_queue: SignalQueue,
    open_trades: list,
    capital: float,
    current_ohlcv: dict,
) -> tuple:
    """
    Process incoming signals and decide which to execute this cycle.

    Logic:
    1. Sync slot tracker from open trades
    2. Drop stale queued signals
    3. Check capital availability
    4. For each new signal (sorted by score):
       - If slot available → mark for execution
       - If slot full → queue it
    5. Check queue for any signals that can now fill a free slot

    Returns (execute_list, updated_queue, updated_slot_tracker)
    """
    execute_list = []

    # Step 1 — Sync slot state
    slot_tracker.sync_from_trades(open_trades)

    # Step 2 — Drop stale signals
    signal_queue.drop_stale(current_ohlcv)

    # Step 3 — Capital check
    if not can_deploy_capital(capital, open_trades):
        logger.info("Capital at 95% capacity — no new trades this cycle")
        return [], signal_queue, slot_tracker

    open_symbols = {t["symbol"] for t in open_trades}

    # Step 4 — Process new signals
    for signal in signals:
        tier   = signal["tier"]
        symbol = signal["symbol"]

        if symbol in open_symbols:
            continue

        if any(e["symbol"] == symbol for e in execute_list):
            continue

        if slot_tracker.has_slot_available(tier):
            execute_list.append(signal)
            slot_tracker.increment(tier)
            logger.info(
                f"Signal accepted: {symbol.replace('/USDT:USDT','')} "
                f"{signal['direction']} | {tier} | "
                f"Slots: {slot_tracker.summary()}"
            )
        else:
            signal_queue.add(signal)

    # Step 5 — Process queue for any free slots
    for tier in ["tier1", "tier2", "tier3"]:
        while (
            slot_tracker.has_slot_available(tier)
            and can_deploy_capital(capital, open_trades)
        ):
            queued = signal_queue.get_next_for_tier(tier)
            if not queued:
                break

            symbol = queued["symbol"]

            if symbol in open_symbols or any(e["symbol"] == symbol for e in execute_list):
                signal_queue.remove(queued)
                continue

            execute_list.append(queued)
            signal_queue.remove(queued)
            slot_tracker.increment(tier)
            logger.info(
                f"Queued signal executed: {symbol.replace('/USDT:USDT','')} "
                f"{queued['direction']} | {tier}"
            )
            try:
                from bot.config import apex_logger
                apex_logger.signal_queue_promoted(token=symbol, tier=tier, wait_cycles=1)
            except Exception:
                pass

    if execute_list:
        logger.info(
            f"Executing {len(execute_list)} signal(s): "
            f"{[s['symbol'].replace('/USDT:USDT','') for s in execute_list]}"
        )

    return execute_list, signal_queue, slot_tracker


# =============================================================================
# SLOT RELEASE
# =============================================================================

def release_slot(slot_tracker: SlotTracker, tier: str, symbol: str):
    """Release a slot when a trade closes."""
    slot_tracker.decrement(tier)
    logger.info(
        f"Slot released: {symbol.replace('/USDT:USDT','')} | {tier} | "
        f"Slots now: {slot_tracker.summary()}"
    )


# =============================================================================
# EXECUTION PREPARATION
# =============================================================================

def prepare_execution(signal: dict) -> Optional[dict]:
    """
    Add position size and leverage to a signal before execution.
    Returns enriched signal dict or None if sizing fails.
    """
    try:
        entry     = signal["entry"]
        sl        = signal["stop_loss"]
        size_mult = signal.get("size_multiplier", 1.0)

        sl_dist_pct = abs(entry - sl) / entry
        if sl_dist_pct <= 0:
            logger.warning(f"Invalid SL distance for {signal['symbol']}")
            return None

        position_usdt    = calculate_position_size(size_mult)
        leverage         = calculate_leverage(sl_dist_pct)
        effective_capital = position_usdt * leverage
        quantity          = effective_capital / entry

        return {
            **signal,
            "position_size_usdt": position_usdt,
            "leverage":           leverage,
            "quantity":           round(quantity, 6),
            "sl_distance_pct":    round(sl_dist_pct * 100, 4),
            "prepared_at":        datetime.now(timezone.utc).isoformat(),
        }

    except Exception as e:
        logger.error(f"Execution prep failed for {signal['symbol']}: {e}")
        return None


# =============================================================================
# STATUS REPORTING
# =============================================================================

def get_capital_status(
    capital: float,
    open_trades: list,
    slot_tracker: SlotTracker,
    signal_queue: SignalQueue,
) -> dict:
    """Return full capital status dict for dashboard and logging."""
    util = get_capital_utilisation(capital, open_trades)
    return {
        "capital":         round(capital, 2),
        "deployed":        round(util["deployed"], 2),
        "available":       round(util["available"], 2),
        "reserve":         round(util["reserve"], 2),
        "utilisation_pct": round(util["utilisation_pct"], 1),
        "at_capacity":     util["at_capacity"],
        "slots":           slot_tracker.summary(),
        "queue_size":      signal_queue.size(),
        "total_open":      slot_tracker.get_total_open_slots(),
        "total_max":       TOTAL_MAX_SLOTS,
    }


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s"
    )

    tracker = SlotTracker()
    queue   = SignalQueue()

    test_signals = [
        {"symbol": "BTC/USDT:USDT",  "direction": "long",  "tier": "tier1",
         "signal_score": 0.85, "timeframe": "1h", "size_multiplier": 1.0,
         "entry": 50000, "stop_loss": 49000, "take_profit": 52000,
         "sl_distance": 1000, "atr": 500, "rrr": 2.0},
        {"symbol": "ETH/USDT:USDT",  "direction": "short", "tier": "tier2",
         "signal_score": 0.72, "timeframe": "4h", "size_multiplier": 1.0,
         "entry": 3000, "stop_loss": 3100, "take_profit": 2800,
         "sl_distance": 100, "atr": 50, "rrr": 2.0},
    ]

    execute_list, queue, tracker = allocate_signals(
        signals       = test_signals,
        slot_tracker  = tracker,
        signal_queue  = queue,
        open_trades   = [],
        capital       = INITIAL_CAPITAL,
        current_ohlcv = {s["symbol"]: True for s in test_signals},
    )

    print(f"\nSignals to execute: {len(execute_list)}")
    for sig in execute_list:
        prepared = prepare_execution(sig)
        if prepared:
            print(
                f"  {prepared['symbol'].replace('/USDT:USDT','')} "
                f"{prepared['direction']} | "
                f"Size: ${prepared['position_size_usdt']} | "
                f"Leverage: {prepared['leverage']}x"
            )

    print(f"\nStatus: {get_capital_status(INITIAL_CAPITAL, [], tracker, queue)}")

# __APEX_LOGGER_V1__
