import json
import sqlite3

RESULTS_PATH = "backtest_results.json"
DB_PATH = "backtest_data.db"

def main():
    with open(RESULTS_PATH) as f:
        results = json.load(f)

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT symbol, candles, start_date, end_date, years_available FROM data_status WHERE status='ok'")
    data_info = {row[0]: row for row in c.fetchall()}
    conn.close()

    deploy = [(s, r) for s, r in results.items() if r['verdict'] == 'DEPLOY']
    caution = [(s, r) for s, r in results.items() if r['verdict'] == 'CAUTION']
    skip = [(s, r) for s, r in results.items() if r['verdict'] == 'SKIP']

    deploy.sort(key=lambda x: x[1]['validation']['win_rate'] if x[1]['validation'] else 0, reverse=True)

    print("=" * 80)
    print("  BACKTESTING FINAL REPORT")
    print("  5 Years | 1H Timeframe | Walk-Forward Validated")
    print("  Top 100 Tokens by Market Cap — Individual Optimization")
    print("=" * 80)

    print(f"\n{'─'*80}")
    print(f"  DEPLOY LIST ({len(deploy)} tokens)")
    print(f"  These tokens have validated strategies — safe to trade")
    print(f"{'─'*80}")
    print(f"{'Token':<18} {'Strategy':<25} {'Train WR':<10} {'Val WR':<10} {'Val Exp':<10} {'Val DD':<8} {'Trades':<8} {'Data'}")
    print(f"{'─'*80}")

    for symbol, r in deploy:
        m = r['metrics']
        v = r['validation']
        info = data_info.get(symbol, (None, None, None, None, 0))
        years = info[4] if info else 0
        token = symbol.replace('/USDT:USDT', '')
        print(f"{token:<18} {r['strategy_name']:<25} {m['win_rate']:<10} {v['win_rate']:<10} ${v['expectancy']:<9} {v['max_drawdown']:<8} {v['total_trades']:<8} {years:.1f}y")

    print(f"\n{'─'*80}")
    print(f"  CAUTION LIST ({len(caution)} tokens)")
    print(f"  Positive expectancy but lower confidence — trade with 50% position size")
    print(f"{'─'*80}")
    print(f"{'Token':<18} {'Strategy':<25} {'Train WR':<10} {'Val WR':<10} {'Val Exp':<10} {'Val DD'}")
    print(f"{'─'*80}")

    for symbol, r in caution:
        if r['metrics'] and r['validation']:
            m = r['metrics']
            v = r['validation']
            token = symbol.replace('/USDT:USDT', '')
            print(f"{token:<18} {r['strategy_name']:<25} {m['win_rate']:<10} {v['win_rate']:<10} ${v['expectancy']:<9} {v['max_drawdown']}")

    print(f"\n{'─'*80}")
    print(f"  SKIP LIST ({len(skip)} tokens)")
    print(f"  No strategy passed walk-forward validation — do not trade")
    print(f"{'─'*80}")
    skips = [s.replace('/USDT:USDT', '') for s, _ in skip]
    print(", ".join(skips))

    print(f"\n{'─'*80}")
    print("  SUMMARY STATISTICS")
    print(f"{'─'*80}")

    if deploy:
        avg_train_wr = sum(r['metrics']['win_rate'] for _, r in deploy) / len(deploy)
        avg_val_wr = sum(r['validation']['win_rate'] for _, r in deploy) / len(deploy)
        avg_exp = sum(r['validation']['expectancy'] for _, r in deploy) / len(deploy)
        avg_dd = sum(r['validation']['max_drawdown'] for _, r in deploy) / len(deploy)
        print(f"  Avg training win rate    : {avg_train_wr:.1f}%")
        print(f"  Avg validation win rate  : {avg_val_wr:.1f}%")
        print(f"  Avg validation expectancy: ${avg_exp:.2f} per trade")
        print(f"  Avg validation drawdown  : {avg_dd:.1f}%")
        print(f"  Overfitting check        : {abs(avg_train_wr - avg_val_wr):.1f}% gap (under 10% is good)")

    print(f"\n{'─'*80}")
    print("  STRATEGY USAGE BREAKDOWN")
    print(f"{'─'*80}")

    strat_counts = {}
    for _, r in deploy:
        name = r['strategy_name']
        strat_counts[name] = strat_counts.get(name, 0) + 1

    for strat, count in sorted(strat_counts.items(), key=lambda x: -x[1]):
        bar = '█' * count
        print(f"  {strat:<30}: {count:>3} tokens  {bar}")

    print(f"\n{'─'*80}")
    print("  WHAT TO DO WITH THESE RESULTS")
    print(f"{'─'*80}")
    print(f"  1. Update strategy.py to use per-token strategies from DEPLOY list")
    print(f"  2. Add CAUTION tokens with 50% position size")
    print(f"  3. Remove SKIP tokens from trading universe")
    print(f"  4. Re-run backtest every 3 months to catch regime changes")
    print(f"\n{'='*80}")
    print("  END OF REPORT")
    print(f"{'='*80}")

if __name__ == "__main__":
    main()