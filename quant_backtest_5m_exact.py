"""
Institutional Quant Backtest: Exact 5-Minute Breakout Candle Strategy.
Evaluates entry execution at the EXACT 5-minute candle where Chartink breakout condition triggered:
- Baseline 5M Entry vs Time-Gated vs Volume-Surge-Filtered Institutional Models
- Comprehensive Institutional Risk Metrics: Sharpe, Sortino, Calmar, Omega, VaR, CVaR
- 2,500-Iteration Monte Carlo Stress Testing
- Walk-Forward In-Sample (70%) vs Out-of-Sample (30%) Robustness Validation
- Slippage & Execution Friction Stress Curves
- High-Resolution Institutional Performance Visualization Dashboard
"""

import os
from pathlib import Path
import logging
import time
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
from scipy import stats
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("quant_5m_exact")

TRADES_CSV = Path("data/quant_new_screener_trades.csv")
OUT_SUMMARY_CSV = Path("data/quant_5m_exact_summary.csv")
OUT_MC_CSV = Path("data/quant_5m_exact_monte_carlo.csv")
OUT_DASHBOARD_PNG = Path("data/quant_5m_exact_dashboard.png")


def compute_quant_metrics(pnl_series: pd.Series, dates_series: pd.Series, rf_annual: float = 0.065) -> dict:
    """Computes rigorous institutional risk-adjusted performance metrics."""
    n = len(pnl_series)
    if n == 0:
        return {}

    wins = pnl_series[pnl_series > 0]
    losses = pnl_series[pnl_series < 0]
    win_rate = (len(wins) / n) * 100.0

    total_gain = wins.sum() if len(wins) > 0 else 0.0
    total_loss = abs(losses.sum()) if len(losses) > 0 else 1e-6
    profit_factor = total_gain / total_loss

    avg_win = wins.mean() if len(wins) > 0 else 0.0
    avg_loss = abs(losses.mean()) if len(losses) > 0 else 0.0
    payoff_ratio = (avg_win / avg_loss) if avg_loss > 0 else 0.0
    expectancy = (win_rate / 100.0 * avg_win) - ((100.0 - win_rate) / 100.0 * avg_loss)

    # Daily aggregation for Sharpe / Sortino
    df_d = pd.DataFrame({"date": dates_series, "pnl": pnl_series})
    daily_sums = df_d.groupby("date")["pnl"].sum()
    n_days = max(1, len(daily_sums))

    mean_daily = daily_sums.mean() / 100.0
    std_daily = daily_sums.std() / 100.0 if n_days > 1 else 0.0001
    std_daily = max(std_daily, 0.0001)

    rf_daily = rf_annual / 252.0
    sharpe = ((mean_daily - rf_daily) / std_daily) * np.sqrt(252.0)

    downside = daily_sums[daily_sums < 0] / 100.0
    downside_std = np.sqrt(np.mean(downside ** 2)) * np.sqrt(252.0) if len(downside) > 0 else 0.0001
    sortino = (mean_daily * 252.0) / downside_std if downside_std > 0 else 99.0

    # Cumulative Drawdown
    cum_ret = pnl_series.cumsum()
    peak = cum_ret.cummax()
    dd = cum_ret - peak
    max_dd = abs(dd.min())

    total_pnl = pnl_series.sum()
    cagr = ((1.0 + total_pnl / 100.0) ** (252.0 / n_days) - 1.0) * 100.0 if n_days > 0 and total_pnl > -100 else 0.0
    calmar = (cagr / max_dd) if max_dd > 0 else 99.0

    # Tail Risk
    var_95 = float(np.percentile(pnl_series, 5))
    var_99 = float(np.percentile(pnl_series, 1))
    cvar_95 = float(pnl_series[pnl_series <= var_95].mean()) if len(pnl_series[pnl_series <= var_95]) > 0 else var_95
    cvar_99 = float(pnl_series[pnl_series <= var_99].mean()) if len(pnl_series[pnl_series <= var_99]) > 0 else var_99

    skewness = float(stats.skew(pnl_series))
    kurt = float(stats.kurtosis(pnl_series))

    pos_ret = pnl_series[pnl_series > 0].sum()
    neg_ret = abs(pnl_series[pnl_series < 0].sum())
    omega = (pos_ret / neg_ret) if neg_ret > 0 else 99.0

    return {
        "trades": n,
        "win_rate": round(win_rate, 2),
        "profit_factor": round(profit_factor, 2),
        "total_pnl": round(total_pnl, 2),
        "expectancy": round(expectancy, 3),
        "payoff_ratio": round(payoff_ratio, 2),
        "sharpe": round(sharpe, 2),
        "sortino": round(sortino, 2),
        "calmar": round(calmar, 2),
        "omega": round(omega, 2),
        "max_dd": round(max_dd, 2),
        "cagr": round(cagr, 2),
        "var_95": round(var_95, 2),
        "var_99": round(var_99, 2),
        "cvar_95": round(cvar_95, 2),
        "cvar_99": round(cvar_99, 2),
        "skewness": round(skewness, 2),
        "kurtosis": round(kurt, 2),
    }


def run_monte_carlo(pnl_series: pd.Series, n_sims: int = 2500) -> Tuple[pd.DataFrame, dict]:
    """Runs 2,500-iteration Monte Carlo permutation stress test."""
    pnl_arr = pnl_series.to_numpy()
    n_trades = len(pnl_arr)

    equity_paths = np.zeros((n_sims, n_trades + 1))
    max_drawdowns = np.zeros(n_sims)
    win_rates = np.zeros(n_sims)
    final_pnls = np.zeros(n_sims)

    for i in range(n_sims):
        sampled = np.random.choice(pnl_arr, size=n_trades, replace=True)
        eq = np.concatenate([[0.0], np.cumsum(sampled)])
        equity_paths[i, :] = eq

        peak = np.maximum.accumulate(eq)
        dd = eq - peak
        max_drawdowns[i] = abs(np.min(dd))
        win_rates[i] = (np.sum(sampled > 0) / n_trades) * 100.0
        final_pnls[i] = eq[-1]

    percentiles = [5, 25, 50, 75, 95]
    mc_curves = {f"p{p}": np.percentile(equity_paths, p, axis=0) for p in percentiles}
    df_mc = pd.DataFrame(mc_curves)

    stats_summary = {
        "pnl_p5": round(float(np.percentile(final_pnls, 5)), 2),
        "pnl_p50": round(float(np.percentile(final_pnls, 50)), 2),
        "pnl_p95": round(float(np.percentile(final_pnls, 95)), 2),
        "winrate_p5": round(float(np.percentile(win_rates, 5)), 2),
        "winrate_p50": round(float(np.percentile(win_rates, 50)), 2),
        "winrate_p95": round(float(np.percentile(win_rates, 95)), 2),
        "dd_p5": round(float(np.percentile(max_drawdowns, 5)), 2),
        "dd_p50": round(float(np.percentile(max_drawdowns, 50)), 2),
        "dd_p95": round(float(np.percentile(max_drawdowns, 95)), 2),
        "prob_ruin_5pct": round(float(np.mean(max_drawdowns >= 5.0) * 100.0), 2),
        "prob_ruin_10pct": round(float(np.mean(max_drawdowns >= 10.0) * 100.0), 2),
    }

    return df_mc, stats_summary


def generate_dashboard(
    models_dict: Dict[str, pd.DataFrame],
    best_mc_df: pd.DataFrame,
    walk_forward_df: pd.DataFrame,
    out_path: Path,
):
    """Generates an 8-panel high-resolution institutional dashboard chart."""
    plt.style.use("dark_background")
    fig, axes = plt.subplots(4, 2, figsize=(18, 22), dpi=150)
    fig.patch.set_facecolor("#0b0f19")

    title_text = (
        "EXACT 5-MINUTE BREAKOUT CANDLE STRATEGY: INSTITUTIONAL QUANT EVALUATION\n"
        "Nifty 500 Broad Market Cash Equity | Exact Earliest 5M Trigger Bar | Quant Filter Comparison"
    )
    fig.suptitle(title_text, fontsize=16, fontweight="bold", color="#38bdf8", y=0.99)

    colors = {
        "Baseline (All 5M Entries)": "#ef4444",
        "Time-Gated (Post-10:00 AM)": "#f59e0b",
        "Quant Model: Post-10 AM + Vol Surge >= 1.1x": "#10b981",
        "Quant Model: Post-10 AM + Vol Surge >= 1.3x": "#38bdf8",
        "Sub 1 Monthly Breakouts (Post-10 AM)": "#a855f7",
    }

    # 1. Equity Curves Comparison
    ax1 = axes[0, 0]
    ax1.set_facecolor("#111827")
    for name, df_v in models_dict.items():
        clr = colors.get(name, "#9ca3af")
        cum = df_v["m1_pnl"].cumsum().reset_index(drop=True)
        ax1.plot(cum, label=f"{name} ({cum.iloc[-1]:+.1f}%)", color=clr, lw=2.0 if "1.1x" in name or "1.3x" in name else 1.4)
    ax1.axhline(0, color="#6b7280", ls="--", lw=1)
    ax1.set_title("Cumulative Equity Curves (%): Baseline vs Quant Filters", fontsize=12, fontweight="bold", color="#f3f4f6")
    ax1.set_ylabel("Cumulative PnL (%)", color="#9ca3af")
    ax1.grid(True, alpha=0.2, ls="--")
    ax1.legend(loc="upper left", framealpha=0.3, fontsize=8)

    # 2. Drawdown Underwater Curves
    ax2 = axes[0, 1]
    ax2.set_facecolor("#111827")
    for name, df_v in models_dict.items():
        clr = colors.get(name, "#9ca3af")
        cum = df_v["m1_pnl"].cumsum().reset_index(drop=True)
        peak = cum.cummax()
        dd = cum - peak
        ax2.plot(dd, label=f"{name} (Max DD: {abs(dd.min()):.1f}%)", color=clr, lw=1.5, alpha=0.85)
    ax2.set_title("Underwater Drawdown (%): Tail Risk Compression", fontsize=12, fontweight="bold", color="#f3f4f6")
    ax2.set_ylabel("Drawdown (%)", color="#9ca3af")
    ax2.grid(True, alpha=0.2, ls="--")
    ax2.legend(loc="lower left", framealpha=0.3, fontsize=8)

    # 3. PnL Distribution: Winning Model vs Baseline
    ax3 = axes[1, 0]
    ax3.set_facecolor("#111827")
    base_pnl = models_dict["Baseline (All 5M Entries)"]["m1_pnl"]
    win_pnl = models_dict["Quant Model: Post-10 AM + Vol Surge >= 1.1x"]["m1_pnl"]
    ax3.hist(base_pnl, bins=35, color="#ef4444", alpha=0.35, label=f"Baseline 5M (WR: {(base_pnl>0).mean()*100:.1f}%)", density=True)
    ax3.hist(win_pnl, bins=25, color="#10b981", alpha=0.75, label=f"Quant 1.1x (WR: {(win_pnl>0).mean()*100:.1f}%)", density=True)
    ax3.axvline(win_pnl.mean(), color="#34d399", ls="-", lw=2, label=f"Quant Mean: {win_pnl.mean():+.2f}%")
    ax3.axvline(base_pnl.mean(), color="#ef4444", ls="--", lw=1.5, label=f"Base Mean: {base_pnl.mean():+.2f}%")
    ax3.set_title("Trade Return Distribution Shift (Base vs Quant Model)", fontsize=12, fontweight="bold", color="#f3f4f6")
    ax3.set_xlabel("Return per Trade (%)", color="#9ca3af")
    ax3.grid(True, alpha=0.2, ls="--")
    ax3.legend(loc="upper right", framealpha=0.3, fontsize=8)

    # 4. Monte Carlo 2,500 Simulation on Winning Model
    ax4 = axes[1, 1]
    ax4.set_facecolor("#111827")
    x = range(len(best_mc_df))
    ax4.fill_between(x, best_mc_df["p5"], best_mc_df["p95"], color="#10b981", alpha=0.25, label="5% - 95% Confidence Band")
    ax4.fill_between(x, best_mc_df["p25"], best_mc_df["p75"], color="#10b981", alpha=0.45, label="25% - 75% Interquartile")
    ax4.plot(x, best_mc_df["p50"], color="#34d399", lw=2, label="Median Path (50th %ile)")
    ax4.plot(x, best_mc_df["p5"], color="#f59e0b", lw=1.2, ls="--", label="Worst 5% Stress Case")
    ax4.axhline(0, color="#6b7280", ls="--", lw=1)
    ax4.set_title("Monte Carlo 2,500 Permutation Stress Test (Quant Model)", fontsize=12, fontweight="bold", color="#f3f4f6")
    ax4.set_ylabel("Simulated Return (%)", color="#9ca3af")
    ax4.grid(True, alpha=0.2, ls="--")
    ax4.legend(loc="upper left", framealpha=0.3, fontsize=8)

    # 5. Walk-Forward In-Sample vs Out-of-Sample Validation
    ax5 = axes[2, 0]
    ax5.set_facecolor("#111827")
    wf_models = walk_forward_df["Model"].unique()
    x_wf = np.arange(len(wf_models))
    w_w = 0.35
    is_pnl = [walk_forward_df[(walk_forward_df["Model"] == m) & (walk_forward_df["Sample"] == "In-Sample (70%)")]["Total PnL (%)"].values[0] for m in wf_models]
    oos_pnl = [walk_forward_df[(walk_forward_df["Model"] == m) & (walk_forward_df["Sample"] == "Out-of-Sample (30%)")]["Total PnL (%)"].values[0] for m in wf_models]
    ax5.bar(x_wf - w_w/2, is_pnl, width=w_w, label="In-Sample (First 70% Days)", color="#38bdf8", alpha=0.85)
    ax5.bar(x_wf + w_w/2, oos_pnl, width=w_w, label="Out-of-Sample (Last 30% Days)", color="#10b981", alpha=0.85)
    ax5.axhline(0, color="#6b7280", ls="--", lw=1)
    ax5.set_xticks(x_wf)
    ax5.set_xticklabels([m.replace("Quant Model: ", "").replace("Baseline ", "") for m in wf_models], fontsize=8, rotation=15)
    ax5.set_title("Walk-Forward In-Sample vs Out-of-Sample Robustness", fontsize=12, fontweight="bold", color="#f3f4f6")
    ax5.set_ylabel("Cumulative PnL (%)", color="#9ca3af")
    ax5.grid(True, alpha=0.2, ls="--")
    ax5.legend(loc="upper left", framealpha=0.3, fontsize=8)

    # 6. Holding Duration Analysis (5-Minute Bars to Target vs Stop)
    ax6 = axes[2, 1]
    ax6.set_facecolor("#111827")
    win_trades = models_dict["Quant Model: Post-10 AM + Vol Surge >= 1.1x"]
    bars_tp = win_trades[win_trades["m1_exit_reason"] == "TP"]["m1_holding_bars"]
    bars_sl = win_trades[win_trades["m1_exit_reason"] == "SL"]["m1_holding_bars"]
    bars_eod = win_trades[win_trades["m1_exit_reason"] == "EOD"]["m1_holding_bars"]

    ax6.hist(bars_tp, bins=15, color="#10b981", alpha=0.7, label=f"Target Hit (+1.5%) - Mean: {bars_tp.mean():.1f} bars (~{bars_tp.mean()*5:.0f}m)")
    ax6.hist(bars_sl, bins=15, color="#ef4444", alpha=0.6, label=f"Stop Hit (-0.8%) - Mean: {bars_sl.mean():.1f} bars (~{bars_sl.mean()*5:.0f}m)")
    ax6.set_title("Trade Execution Velocity (Bars to Exit on Exact 5M Entry)", fontsize=12, fontweight="bold", color="#f3f4f6")
    ax6.set_xlabel("Holding Duration (5-Minute Bars)", color="#9ca3af")
    ax6.set_ylabel("Trade Count", color="#9ca3af")
    ax6.grid(True, alpha=0.2, ls="--")
    ax6.legend(loc="upper right", framealpha=0.3, fontsize=8)

    # 7. Win Rate & Profit Factor Comparison
    ax7 = axes[3, 0]
    ax7.set_facecolor("#111827")
    model_names_short = [k.replace("Quant Model: ", "").replace("Baseline ", "") for k in models_dict.keys()]
    win_rates = [(df_v["m1_pnl"] > 0).mean() * 100 for df_v in models_dict.values()]
    profit_factors = [df_v[df_v["m1_pnl"] > 0]["m1_pnl"].sum() / abs(df_v[df_v["m1_pnl"] < 0]["m1_pnl"].sum()) if abs(df_v[df_v["m1_pnl"] < 0]["m1_pnl"].sum()) > 0 else 0 for df_v in models_dict.values()]

    x_m = np.arange(len(model_names_short))
    ax7.bar(x_m - 0.18, win_rates, width=0.35, label="Win Rate (%)", color="#10b981", alpha=0.85)
    ax7_twin = ax7.twinx()
    ax7_twin.plot(x_m + 0.18, profit_factors, color="#f59e0b", marker="s", lw=2, label="Profit Factor")
    ax7_twin.axhline(1.0, color="#ef4444", ls=":", lw=1.2, label="Break-even PF (1.0)")
    ax7.set_xticks(x_m)
    ax7.set_xticklabels(model_names_short, fontsize=8, rotation=15)
    ax7.set_ylabel("Win Rate (%)", color="#10b981")
    ax7_twin.set_ylabel("Profit Factor", color="#f59e0b")
    ax7.set_title("Win Rate & Profit Factor by Strategy Model", fontsize=12, fontweight="bold", color="#f3f4f6")
    ax7.grid(True, alpha=0.2, ls="--")

    # 8. Friction Sensitivity Stress Testing
    ax8 = axes[3, 1]
    ax8.set_facecolor("#111827")
    slippage_levels = np.linspace(0.02, 0.20, 10)  # 0.02% to 0.20% per trade
    for name, df_v in [
        ("Baseline 5M Entry", models_dict["Baseline (All 5M Entries)"]),
        ("Quant Model (1.1x Vol)", models_dict["Quant Model: Post-10 AM + Vol Surge >= 1.1x"]),
        ("Quant Model (1.3x Vol)", models_dict["Quant Model: Post-10 AM + Vol Surge >= 1.3x"]),
    ]:
        stressed = []
        for slip in slippage_levels:
            adj = df_v["m1_pnl"] - (slip * 2.0)
            stressed.append(adj.sum())
        ax8.plot(slippage_levels * 100, stressed, label=name, marker="o", lw=1.8, color=colors.get(name, "#38bdf8"))

    ax8.axhline(0, color="#6b7280", ls="--", lw=1)
    ax8.set_title("Slippage & Transaction Friction Sensitivity Stress Curve", fontsize=12, fontweight="bold", color="#f3f4f6")
    ax8.set_xlabel("One-Way Slippage / Brokerage (%)", color="#9ca3af")
    ax8.set_ylabel("Net PnL (%)", color="#9ca3af")
    ax8.grid(True, alpha=0.2, ls="--")
    ax8.legend(loc="upper right", framealpha=0.3, fontsize=8)

    plt.tight_layout(rect=[0, 0.02, 1, 0.97])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out_path, dpi=150, facecolor=fig.get_facecolor(), edgecolor="none")
    plt.close()
    logger.info(f"Saved institutional visual dashboard to {out_path}")


def main():
    logger.info("Starting Exact 5-Minute Breakout Candle Quant-Level Backtest...")
    if not TRADES_CSV.exists():
        logger.error(f"Trades file {TRADES_CSV} not found! Run backtest first.")
        return

    df_trades = pd.read_csv(TRADES_CSV)
    logger.info(f"Loaded {len(df_trades)} trades across Nifty 500 stocks.")

    # Define exact 5M entry models
    models = {
        "Baseline (All 5M Entries)": df_trades,
        "Time-Gated (Post-10:00 AM)": df_trades[df_trades["trigger_time"] >= "10:00:00"],
        "Quant Model: Post-10 AM + Vol Surge >= 1.1x": df_trades[(df_trades["trigger_time"] >= "10:00:00") & (df_trades["vol_surge_ratio"] >= 1.1)],
        "Quant Model: Post-10 AM + Vol Surge >= 1.3x": df_trades[(df_trades["trigger_time"] >= "10:00:00") & (df_trades["vol_surge_ratio"] >= 1.3)],
        "Sub 1 Monthly Breakouts (Post-10 AM)": df_trades[(df_trades["trigger_time"] >= "10:00:00") & (df_trades["primary_strategy"].str.contains("Monthly"))],
    }

    summary_rows = []
    for name, df_v in models.items():
        metrics = compute_quant_metrics(df_v["m1_pnl"], df_v["date"])
        summary_rows.append({"Model": name, **metrics})

    df_summary = pd.DataFrame(summary_rows)
    df_summary.to_csv(OUT_SUMMARY_CSV, index=False)
    logger.info(f"Saved quant summary to {OUT_SUMMARY_CSV}")

    print("\n" + "=" * 90)
    print("   EXACT 5-MINUTE CANDLE BREAKOUT: INSTITUTIONAL QUANT PERFORMANCE REPORT")
    print("=" * 90)
    print(df_summary.to_string(index=False))
    print("=" * 90 + "\n")

    # Walk-Forward Analysis (First 70% of days vs Last 30% of days)
    unique_dates = sorted(df_trades["date"].unique())
    split_idx = int(len(unique_dates) * 0.70)
    is_dates = set(unique_dates[:split_idx])
    oos_dates = set(unique_dates[split_idx:])

    wf_rows = []
    for name, df_v in models.items():
        df_is = df_v[df_v["date"].isin(is_dates)]
        df_oos = df_v[df_v["date"].isin(oos_dates)]
        
        is_pnl = df_is["m1_pnl"].sum()
        is_wr = (df_is["m1_pnl"] > 0).mean() * 100 if len(df_is) > 0 else 0
        oos_pnl = df_oos["m1_pnl"].sum()
        oos_wr = (df_oos["m1_pnl"] > 0).mean() * 100 if len(df_oos) > 0 else 0

        wf_rows.append({"Model": name, "Sample": "In-Sample (70%)", "Trades": len(df_is), "Win Rate (%)": round(is_wr, 1), "Total PnL (%)": round(is_pnl, 2)})
        wf_rows.append({"Model": name, "Sample": "Out-of-Sample (30%)", "Trades": len(df_oos), "Win Rate (%)": round(oos_wr, 1), "Total PnL (%)": round(oos_pnl, 2)})

    df_wf = pd.DataFrame(wf_rows)
    print("--- WALK-FORWARD IN-SAMPLE VS OUT-OF-SAMPLE VALIDATION ---")
    print(df_wf.to_string(index=False))
    print("----------------------------------------------------------\n")

    # Monte Carlo 2,500 Simulation on Winning Model
    best_df = models["Quant Model: Post-10 AM + Vol Surge >= 1.1x"]
    logger.info("Running 2,500-iteration Monte Carlo Stress Test on Quant Model (Post-10 AM + 1.1x Vol Surge)...")
    df_mc, mc_stats = run_monte_carlo(best_df["m1_pnl"], n_sims=2500)
    df_mc.to_csv(OUT_MC_CSV, index=False)
    logger.info(f"Saved Monte Carlo results to {OUT_MC_CSV}")

    print("--- MONTE CARLO 2,500 PERMUTATION STRESS TEST (QUANT MODEL) ---")
    for k, v in mc_stats.items():
        print(f"  {k:25s}: {v}")
    print("---------------------------------------------------------------\n")

    # Generate Visual Dashboard
    logger.info("Generating high-resolution institutional visual dashboard...")
    generate_dashboard(
        models_dict=models,
        best_mc_df=df_mc,
        walk_forward_df=df_wf,
        out_path=OUT_DASHBOARD_PNG,
    )
    logger.info("Exact 5-minute quant backtest complete!")


if __name__ == "__main__":
    main()
