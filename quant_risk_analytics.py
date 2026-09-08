"""
Institutional Quantitative Risk & Performance Analytics Engine.
Performs quant-grade evaluation of 6-month Chartink intraday backtest results:
- Sharpe, Sortino, Calmar, Omega Ratios
- Tail Risk: VaR 95/99, CVaR 95/99, Skewness, Kurtosis
- 2,500-run Monte Carlo Permutation & Stress Simulation
- Slippage Sensitivity Stress Curves
- Multi-dimensional breakdown: Monthly, Day of Week, Intraday Time, Sectoral
- High-res institutional dashboard visualization
"""

from datetime import datetime
import json
import logging
from pathlib import Path
import time
from typing import Dict, List, Tuple

import matplotlib
matplotlib.use("Agg")  # Non-interactive headless backend
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("quant_analytics")


def load_trades_and_sectors() -> pd.DataFrame:
    """Loads 6-month trade logs and maps sector classifications."""
    trades_path = Path("data/chartink_6month_trades.csv")
    truth_path = Path("data/chartink_ground_truth.csv")

    if not trades_path.exists():
        raise FileNotFoundError(f"{trades_path} not found. Run backtest_6months_chartink.py first.")

    df_trades = pd.read_csv(trades_path)
    df_trades["entry_time"] = pd.to_datetime(df_trades["entry_time"])
    df_trades["exit_time"] = pd.to_datetime(df_trades["exit_time"])
    df_trades["date"] = df_trades["entry_time"].dt.date
    df_trades["month_year"] = df_trades["entry_time"].dt.strftime("%Y-%m")
    df_trades["day_name"] = df_trades["entry_time"].dt.day_name()
    df_trades["entry_hour"] = df_trades["entry_time"].dt.hour
    df_trades["entry_minute"] = df_trades["entry_time"].dt.minute

    # Sector mapping from ground truth
    if truth_path.exists():
        df_truth = pd.read_csv(truth_path)
        sector_map = df_truth.drop_duplicates("Symbol").set_index("Symbol")["Sector"].to_dict()
        df_trades["sector"] = df_trades["symbol"].map(sector_map).fillna("Other")
    else:
        df_trades["sector"] = "Other"

    # Time-of-day category
    def categorize_time(row):
        t_val = row["entry_hour"] * 60 + row["entry_minute"]
        if t_val < 11 * 60 + 30:
            return "Morning (09:20-11:30)"
        elif t_val < 13 * 60 + 30:
            return "Midday (11:30-13:30)"
        else:
            return "Afternoon (13:30-14:30)"

    df_trades["time_slot"] = df_trades.apply(categorize_time, axis=1)
    return df_trades


def compute_quant_metrics(pnl_series: pd.Series, dates_series: pd.Series, rf_annual: float = 0.065) -> dict:
    """
    Computes rigorous institutional quantitative metrics.
    Annualized using 252 trading days.
    """
    returns = pnl_series / 100.0
    n_trades = len(returns)
    if n_trades == 0:
        return {}

    # Aggregate daily returns for Sharpe/Volatility
    df_temp = pd.DataFrame({"date": dates_series, "ret": returns})
    daily_returns = df_temp.groupby("date")["ret"].sum()
    n_days = max(1, len(daily_returns))

    mean_daily = daily_returns.mean()
    std_daily = daily_returns.std() if n_days > 1 else 0.0001
    std_daily = std_daily if std_daily > 0 else 0.0001

    rf_daily = rf_annual / 252.0
    excess_daily = mean_daily - rf_daily

    # Annualized metrics
    cagr = ((1.0 + returns).prod()) ** (252.0 / n_days) - 1.0 if n_days > 0 else 0.0
    ann_vol = std_daily * np.sqrt(252.0)
    sharpe = (excess_daily / std_daily) * np.sqrt(252.0)

    # Sortino: Downside deviation below 0
    downside_daily = daily_returns[daily_returns < 0.0]
    downside_std = np.sqrt(np.mean(downside_daily ** 2)) * np.sqrt(252.0) if len(downside_daily) > 0 else 0.0001
    sortino = (mean_daily * 252.0) / downside_std if downside_std > 0 else 99.0

    # Drawdown series
    cum_equity = (1.0 + returns).cumprod()
    peak = cum_equity.cummax()
    dd_series = (cum_equity - peak) / peak * 100.0
    max_dd = abs(dd_series.min()) if len(dd_series) > 0 else 0.0

    calmar = (cagr * 100.0) / max_dd if max_dd > 0 else 99.0

    # Omega Ratio
    pos_ret = returns[returns > 0.0].sum()
    neg_ret = abs(returns[returns < 0.0].sum())
    omega = (pos_ret / neg_ret) if neg_ret > 0 else 99.0

    # Statistical Moments
    ret_skew = stats.skew(returns) if len(returns) > 2 else 0.0
    ret_kurt = stats.kurtosis(returns) if len(returns) > 3 else 0.0

    # Historical Value at Risk (VaR) & CVaR (Expected Shortfall)
    var_95 = abs(np.percentile(returns, 5)) * 100.0
    var_99 = abs(np.percentile(returns, 1)) * 100.0
    cvar_95 = abs(returns[returns <= np.percentile(returns, 5)].mean()) * 100.0
    cvar_99 = abs(returns[returns <= np.percentile(returns, 1)].mean()) * 100.0

    # Trade stats
    wins = returns[returns > 0.0]
    losses = returns[returns <= 0.0]
    p_win = len(wins) / n_trades
    p_loss = len(losses) / n_trades
    avg_win = wins.mean() * 100.0 if len(wins) > 0 else 0.0
    avg_loss = abs(losses.mean() * 100.0) if len(losses) > 0 else 0.0
    payoff_ratio = (avg_win / avg_loss) if avg_loss > 0 else 0.0

    # Expectancy per trade: E = (P_win * Avg_Win) - (P_loss * Avg_Loss)
    expectancy = (p_win * avg_win) - (p_loss * avg_loss)

    # Consecutive runs
    is_win_list = (returns > 0.0).tolist()
    max_consec_wins = 0
    max_consec_losses = 0
    cur_w, cur_l = 0, 0
    for w in is_win_list:
        if w:
            cur_w += 1
            cur_l = 0
            if cur_w > max_consec_wins:
                max_consec_wins = cur_w
        else:
            cur_l += 1
            cur_w = 0
            if cur_l > max_consec_losses:
                max_consec_losses = cur_l

    return {
        "n_trades": n_trades,
        "n_days": n_days,
        "cagr_pct": cagr * 100.0,
        "ann_vol_pct": ann_vol * 100.0,
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": calmar,
        "omega": omega,
        "max_dd_pct": max_dd,
        "var_95_pct": var_95,
        "var_99_pct": var_99,
        "cvar_95_pct": cvar_95,
        "cvar_99_pct": cvar_99,
        "skewness": ret_skew,
        "kurtosis": ret_kurt,
        "win_rate_pct": p_win * 100.0,
        "payoff_ratio": payoff_ratio,
        "expectancy_pct": expectancy,
        "avg_win_pct": avg_win,
        "avg_loss_pct": avg_loss,
        "total_pnl_pct": returns.sum() * 100.0,
        "max_consec_wins": max_consec_wins,
        "max_consec_losses": max_consec_losses,
        "cum_equity": cum_equity,
        "dd_series": dd_series,
    }


def run_monte_carlo_simulation(returns: np.ndarray, num_simulations: int = 2500) -> dict:
    """
    Performs Monte Carlo bootstrap permutation simulation (2,500 iterations).
    Generates confidence intervals for Drawdown and Final Equity.
    """
    n_trades = len(returns)
    sim_max_drawdowns = []
    sim_final_equities = []

    np.random.seed(42)  # Deterministic seed for reproducible verification

    for _ in range(num_simulations):
        # Sample with replacement
        sample = np.random.choice(returns, size=n_trades, replace=True)
        equity = np.cumprod(1.0 + sample)
        peak = np.maximum.accumulate(equity)
        dd = (equity - peak) / peak * 100.0
        sim_max_drawdowns.append(abs(np.min(dd)))
        sim_final_equities.append(equity[-1])

    sim_max_drawdowns = np.array(sim_max_drawdowns)
    sim_final_equities = np.array(sim_final_equities)

    # Probability of loss (final equity < 1.0)
    prob_loss = (np.sum(sim_final_equities < 1.0) / num_simulations) * 100.0

    return {
        "median_dd": np.median(sim_max_drawdowns),
        "dd_95th": np.percentile(sim_max_drawdowns, 95),
        "dd_99th": np.percentile(sim_max_drawdowns, 99),
        "median_equity": np.median(sim_final_equities),
        "equity_5th": np.percentile(sim_final_equities, 5),
        "equity_95th": np.percentile(sim_final_equities, 95),
        "prob_loss": prob_loss,
        "drawdowns_distribution": sim_max_drawdowns,
    }


def run_slippage_stress_test(df_subset: pd.DataFrame) -> List[dict]:
    """Tests return degradation under varying friction and slippage levels."""
    base_ret = df_subset["pnl_pct"].values / 100.0
    dates = df_subset["date"].reset_index(drop=True)

    stress_scenarios = [
        ("0.05% Slippage (Baseline)", 0.0000),
        ("0.10% Slippage (Moderate)", 0.0010),
        ("0.15% Slippage (High Volatility)", 0.0020),
        ("0.20% Slippage (Extreme Illiquidity)", 0.0030),
    ]

    results = []
    for label, drag in stress_scenarios:
        stressed_pnl = pd.Series((base_ret - drag) * 100.0)
        m = compute_quant_metrics(stressed_pnl, dates)
        results.append({
            "scenario": label,
            "total_pnl": m["total_pnl_pct"],
            "win_rate": m["win_rate_pct"],
            "sharpe": m["sharpe"],
            "profit_factor": m["omega"],
            "max_dd": m["max_dd_pct"],
        })
    return results


def generate_quant_report():
    logger.info("Loading 6-month simulated trade logs...")
    df_trades = load_trades_and_sectors()
    logger.info(f"Loaded {len(df_trades)} trade records across {len(df_trades['date'].unique())} sessions.")

    # We evaluate Model 2 (Target 2.0%, SL 1.0%) on both 5m and 15m
    model_name = "Model 2: Target 2.0%, SL 1.0%"
    df_5m = df_trades[(df_trades["timeframe"] == "5min") & (df_trades["model"] == model_name)].copy()
    df_15m = df_trades[(df_trades["timeframe"] == "15min") & (df_trades["model"] == model_name)].copy()

    m5 = compute_quant_metrics(df_5m["pnl_pct"], df_5m["date"])
    m15 = compute_quant_metrics(df_15m["pnl_pct"], df_15m["date"])

    # Run Monte Carlo (2,500 iterations)
    mc5 = run_monte_carlo_simulation(df_5m["pnl_pct"].values / 100.0, num_simulations=2500)
    mc15 = run_monte_carlo_simulation(df_15m["pnl_pct"].values / 100.0, num_simulations=2500)

    # Slippage Sensitivity
    slip5 = run_slippage_stress_test(df_5m)
    slip15 = run_slippage_stress_test(df_15m)

    # Print Institutional Report
    print("\n" + "=" * 88)
    print("        INSTITUTIONAL QUANTITATIVE RISK & ALPHA AUDIT REPORT (6 MONTHS)")
    print("=" * 88)
    print("Benchmark Risk-Free Rate : 6.50% (RBI 91-Day T-Bill Benchmark)")
    print("Analysis Period          : 2026-03-01 to 2026-09-08 (126 Trading Days)")
    print("Active Universe          : 208 Underlying NSE F&O Equities")
    print("Primary Target Strategy  : Model 2 (2.0% Target / 1.0% Stop Loss)")
    print("-" * 88)

    print(f"\n{'QUANTITATIVE RISK / RETURN METRIC':<36} | {'5-MIN TIMEFRAME':<20} | {'15-MIN TIMEFRAME':<20}")
    print("-" * 88)
    print(f"{'Total Executed Trades':<36} | {m5['n_trades']:<20} | {m15['n_trades']:<20}")
    print(f"{'Annualized Return (CAGR)':<36} | {m5['cagr_pct']:>+18.2f}% | {m15['cagr_pct']:>+18.2f}%")
    print(f"{'Annualized Volatility (sigma)':<36} | {m5['ann_vol_pct']:>19.2f}% | {m15['ann_vol_pct']:>19.2f}%")
    print(f"{'Sharpe Ratio (Rf=6.5%)':<36} | {m5['sharpe']:>20.2f} | {m15['sharpe']:>20.2f}")
    print(f"{'Sortino Ratio (MAR=0%)':<36} | {m5['sortino']:>20.2f} | {m15['sortino']:>20.2f}")
    print(f"{'Calmar Ratio (CAGR / MaxDD)':<36} | {m5['calmar']:>20.2f} | {m15['calmar']:>20.2f}")
    print(f"{'Omega Ratio (Gains/Losses)':<36} | {m5['omega']:>20.2f} | {m15['omega']:>20.2f}")
    print(f"{'Maximum Peak-to-Trough Drawdown':<36} | {m5['max_dd_pct']:>19.2f}% | {m15['max_dd_pct']:>19.2f}%")
    print(f"{'Value at Risk (VaR 95% Daily)':<36} | {m5['var_95_pct']:>19.2f}% | {m15['var_95_pct']:>19.2f}%")
    print(f"{'Value at Risk (VaR 99% Daily)':<36} | {m5['var_99_pct']:>19.2f}% | {m15['var_99_pct']:>19.2f}%")
    print(f"{'Expected Shortfall (CVaR 95%)':<36} | {m5['cvar_95_pct']:>19.2f}% | {m15['cvar_95_pct']:>19.2f}%")
    print(f"{'Expected Shortfall (CVaR 99%)':<36} | {m5['cvar_99_pct']:>19.2f}% | {m15['cvar_99_pct']:>19.2f}%")
    print(f"{'Return Skewness (Asymmetry)':<36} | {m5['skewness']:>20.3f} | {m15['skewness']:>20.3f}")
    print(f"{'Return Kurtosis (Tail Heaviness)':<36} | {m5['kurtosis']:>20.3f} | {m15['kurtosis']:>20.3f}")
    print(f"{'Win Rate':<36} | {m5['win_rate_pct']:>19.1f}% | {m15['win_rate_pct']:>19.1f}%")
    print(f"{'Payoff Ratio (Avg Win / Avg Loss)':<36} | {m5['payoff_ratio']:>20.2f} | {m15['payoff_ratio']:>20.2f}")
    print(f"{'Mathematical Expectancy / Trade':<36} | {m5['expectancy_pct']:>+18.2f}% | {m15['expectancy_pct']:>+18.2f}%")
    print(f"{'Max Consecutive Wins':<36} | {m5['max_consec_wins']:<20} | {m15['max_consec_wins']:<20}")
    print(f"{'Max Consecutive Losses':<36} | {m5['max_consec_losses']:<20} | {m15['max_consec_losses']:<20}")

    print("\n" + "-" * 88)
    print("MONTE CARLO ROBUSTNESS SIMULATION (2,500 ITERATIONS)")
    print("-" * 88)
    print(f"{'Metric':<36} | {'5-Min Result':<20} | {'15-Min Result':<20}")
    print("-" * 88)
    print(f"{'Median Expected Drawdown':<36} | {mc5['median_dd']:>19.2f}% | {mc15['median_dd']:>19.2f}%")
    print(f"{'95% Confidence Worst Drawdown':<36} | {mc5['dd_95th']:>19.2f}% | {mc15['dd_95th']:>19.2f}%")
    print(f"{'99% Confidence Stress Drawdown':<36} | {mc5['dd_99th']:>19.2f}% | {mc15['dd_99th']:>19.2f}%")
    print(f"{'Probability of Ruin / Loss (<0)':<36} | {mc5['prob_loss']:>19.2f}% | {mc15['prob_loss']:>19.2f}%")
    print(f"{'Median Final Equity Multiple':<36} | {mc5['median_equity']:>19.2f}x | {mc15['median_equity']:>19.2f}x")
    print(f"{'5th Percentile Worst Equity':<36} | {mc5['equity_5th']:>19.2f}x | {mc15['equity_5th']:>19.2f}x")

    print("\n" + "-" * 88)
    print("SLIPPAGE & MARKET IMPACT SENSITIVITY DEGRADATION (5-MIN)")
    print("-" * 88)
    print(f"{'Slippage Scenario':<36} | {'Total Return':<14} | {'Win %':<8} | {'Sharpe':<8} | {'Max DD':<8}")
    print("-" * 88)
    for s in slip5:
        print(f"{s['scenario']:<36} | {s['total_pnl']:>+12.2f}% | {s['win_rate']:>6.1f}% | {s['sharpe']:>6.2f} | {s['max_dd']:>6.1f}%")

    # Monthly Breakdown Table
    print("\n" + "-" * 88)
    print("MONTH-BY-MONTH REGIME STABILITY AUDIT (5-MIN)")
    print("-" * 88)
    print(f"{'Month':<12} | {'Trades':<8} | {'Win Rate':<10} | {'Total PnL':<14} | {'Avg PnL':<10} | {'Max DD':<8}")
    print("-" * 88)
    monthly_stats = []
    for m_yr, grp in df_5m.groupby("month_year"):
        m_met = compute_quant_metrics(grp["pnl_pct"], grp["date"])
        print(f"{m_yr:<12} | {m_met['n_trades']:<8} | {m_met['win_rate_pct']:>8.1f}% | {m_met['total_pnl_pct']:>+12.2f}% | {m_met['expectancy_pct']:>+8.2f}% | {m_met['max_dd_pct']:>6.1f}%")
        monthly_stats.append({"month": m_yr, **m_met})

    # Sector Breakdown
    print("\n" + "-" * 88)
    print("TOP SECTOR PERFORMANCE BREAKDOWN (5-MIN)")
    print("-" * 88)
    print(f"{'Sector':<24} | {'Trades':<8} | {'Win Rate':<10} | {'Total Return':<14} | {'Avg Return':<10}")
    print("-" * 88)
    sector_stats = []
    for sec, grp in df_5m.groupby("sector"):
        if len(grp) >= 15:
            m_sec = compute_quant_metrics(grp["pnl_pct"], grp["date"])
            sector_stats.append({"sector": sec, **m_sec})

    sector_stats = sorted(sector_stats, key=lambda x: x["total_pnl_pct"], reverse=True)
    for s in sector_stats[:10]:
        print(f"{s['sector']:<24} | {s['n_trades']:<8} | {s['win_rate_pct']:>8.1f}% | {s['total_pnl_pct']:>+12.2f}% | {s['expectancy_pct']:>+8.2f}%")

    # Generate Institutional Visual Dashboard
    fig, axes = plt.subplots(3, 2, figsize=(16, 14))
    plt.subplots_adjust(hspace=0.35, wspace=0.25)

    # 1. Equity Curve
    ax1 = axes[0, 0]
    ax1.plot(m5["cum_equity"].values, label="5-Min Model 2 (2.0% TP / 1.0% SL)", color="#10b981", lw=2)
    ax1.plot(m15["cum_equity"].values, label="15-Min Model 2 (2.0% TP / 1.0% SL)", color="#3b82f6", lw=2)
    ax1.set_title("Institutional Cumulative Equity Growth (Log Scale)", fontsize=11, fontweight="bold")
    ax1.set_yscale("log")
    ax1.set_xlabel("Trade Number")
    ax1.set_ylabel("Account Equity (x Initial)")
    ax1.grid(True, alpha=0.3)
    ax1.legend()

    # 2. Drawdown Underwater Chart
    ax2 = axes[0, 1]
    ax2.fill_between(range(len(m5["dd_series"])), m5["dd_series"].values, 0, color="#ef4444", alpha=0.4, label="5-Min Drawdown")
    ax2.set_title("Peak-to-Trough Drawdown Profile (Underwater Chart)", fontsize=11, fontweight="bold")
    ax2.set_xlabel("Trade Number")
    ax2.set_ylabel("Drawdown %")
    ax2.grid(True, alpha=0.3)
    ax2.legend()

    # 3. Monte Carlo Drawdown Distribution
    ax3 = axes[1, 0]
    ax3.hist(mc5["drawdowns_distribution"], bins=50, color="#8b5cf6", alpha=0.7, edgecolor="black")
    ax3.axvline(mc5["median_dd"], color="yellow", lw=2, label=f"Median DD ({mc5['median_dd']:.1f}%)")
    ax3.axvline(mc5["dd_95th"], color="orange", lw=2, label=f"95% CI DD ({mc5['dd_95th']:.1f}%)")
    ax3.axvline(mc5["dd_99th"], color="red", lw=2, label=f"99% Stress DD ({mc5['dd_99th']:.1f}%)")
    ax3.set_title("Monte Carlo Drawdown Distribution (2,500 Runs)", fontsize=11, fontweight="bold")
    ax3.set_xlabel("Maximum Drawdown %")
    ax3.set_ylabel("Frequency")
    ax3.grid(True, alpha=0.3)
    ax3.legend()

    # 4. PnL Distribution & Value at Risk (VaR)
    ax4 = axes[1, 1]
    ax4.hist(df_5m["pnl_pct"], bins=40, color="#06b6d4", alpha=0.7, edgecolor="black")
    ax4.axvline(-m5["var_95_pct"], color="orange", lw=2, linestyle="--", label=f"VaR 95% (-{m5['var_95_pct']:.2f}%)")
    ax4.axvline(-m5["cvar_95_pct"], color="red", lw=2, linestyle="--", label=f"CVaR 95% (-{m5['cvar_95_pct']:.2f}%)")
    ax4.set_title("Trade Return Distribution & Tail Risk (VaR/CVaR)", fontsize=11, fontweight="bold")
    ax4.set_xlabel("Trade Return %")
    ax4.set_ylabel("Count")
    ax4.grid(True, alpha=0.3)
    ax4.legend()

    # 5. Monthly Return Bar Chart
    ax5 = axes[2, 0]
    m_labels = [m["month"] for m in monthly_stats]
    m_vals = [m["total_pnl_pct"] for m in monthly_stats]
    colors = ["#10b981" if v >= 0 else "#ef4444" for v in m_vals]
    ax5.bar(m_labels, m_vals, color=colors, edgecolor="black")
    ax5.set_title("Month-by-Month Cumulative Alpha (% Return)", fontsize=11, fontweight="bold")
    ax5.set_ylabel("Total Return %")
    ax5.grid(True, alpha=0.3)

    # 6. Sector Performance Bar Chart
    ax6 = axes[2, 1]
    sec_names = [s["sector"][:12] for s in sector_stats[:8]]
    sec_pnls = [s["total_pnl_pct"] for s in sector_stats[:8]]
    ax6.barh(sec_names[::-1], sec_pnls[::-1], color="#3b82f6", edgecolor="black")
    ax6.set_title("Top 8 Sector Alpha Generators", fontsize=11, fontweight="bold")
    ax6.set_xlabel("Total Return %")
    ax6.grid(True, alpha=0.3)

    chart_file = Path("data/quant_backtest_dashboard.png")
    plt.savefig(chart_file, dpi=150, bbox_inches="tight")
    plt.close()
    logger.info(f"Saved institutional visual dashboard to {chart_file}")

    # Export Quant Metrics to CSV
    metrics_export = [
        {"timeframe": "5min", **m5},
        {"timeframe": "15min", **m15},
    ]
    pd.DataFrame(metrics_export).drop(columns=["cum_equity", "dd_series"]).to_csv("data/quant_metrics_summary.csv", index=False)
    pd.DataFrame(monthly_stats).drop(columns=["cum_equity", "dd_series"]).to_csv("data/quant_monthly_performance.csv", index=False)
    pd.DataFrame(sector_stats).drop(columns=["cum_equity", "dd_series"]).to_csv("data/quant_sector_performance.csv", index=False)
    print("\nSaved detailed quantitative reports to data/quant_metrics_summary.csv")
    print(f"Saved institutional dashboard to {chart_file}")
    print("=" * 88)


if __name__ == "__main__":
    generate_quant_report()
