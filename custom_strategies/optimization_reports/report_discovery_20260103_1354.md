# Strategy Optimization Report: Discovery

**Date**: 2026-01-03 21:25
**Analysis Window**: Last 360 days
**Resolution**: 1m (High Precision)

## Technical Summary
- **Turbo Mode**: Enabled ⚡
- **Concurrency**: 2 Workers | 250 Batch Size
- **Simulations**: 100000 successful / 100000 sent (100.0% success)
- **Total Time**: 450m 7s
- **Efficiency**: 0.27s per successful sim

## Top Performers (Holy Grails)
> **Criteria**: PnL > 15% , Max Drawdown < 20%

| Pair     | Config                               |   PnL |   Drawdown |   Sharpe |
|:---------|:-------------------------------------|------:|-----------:|---------:|
| ADA-USDT | Fast 15/Slow 30/1h (SL 0.02/TP 0.15) | 79.34 |     -14.40 |     1.09 |
| ADA-USDT | Fast 15/Slow 30/1h (SL 0.02/TP 0.02) | 77.08 |     -11.87 |     1.34 |
| ADA-USDT | Fast 15/Slow 30/1h (SL 0.02/TP 0.05) | 72.15 |     -14.32 |     0.86 |
| ADA-USDT | Fast 15/Slow 30/1h (SL 0.02/TP 0.05) | 72.15 |     -14.32 |     0.86 |
| ADA-USDT | Fast 15/Slow 30/1h (SL 0.1/TP 0.15)  | 65.17 |     -15.09 |     0.97 |
| ADA-USDT | Fast 15/Slow 30/1h (SL 0.04/TP 0.02) | 64.46 |     -15.01 |     1.11 |
| ADA-USDT | Fast 15/Slow 30/1h (SL 0.1/TP 0.2)   | 63.56 |     -15.09 |     0.87 |
| ADA-USDT | Fast 15/Slow 30/1h (SL 0.04/TP 0.2)  | 60.58 |     -18.61 |     0.88 |
| ADA-USDT | Fast 15/Slow 30/1h (SL 0.04/TP 0.2)  | 60.58 |     -18.61 |     0.88 |
| ADA-USDT | Fast 15/Slow 30/1h (SL 0.04/TP 0.2)  | 60.58 |     -18.61 |     0.88 |

## Sweet Spot Analysis (Robust Parameter Clusters)
> **Why this matters**: Individual peaks are often overfitting. We look for 'Sweet Spots'—ranges of parameters that perform consistently well across multiple tests.

### Top MA Range Clusters
| Pair      | Interval   |   Fast_Bucket |   Slow_Bucket |   mean |   count |
|:----------|:-----------|--------------:|--------------:|-------:|--------:|
| ADA-USDT  | 1h         |            15 |            20 |  56.69 |      24 |
| SOL-USDT  | 1h         |            15 |            20 |  42.19 |      20 |
| SOL-USDT  | 1h         |            20 |            60 |  40.84 |      35 |
| SOL-USDT  | 1h         |            10 |            20 |  38.71 |      32 |
| LINK-USDT | 1h         |            10 |            20 |  34.21 |      17 |
| LINK-USDT | 1h         |            55 |           100 |  32.62 |      58 |
| SOL-USDT  | 1h         |            25 |            40 |  32.61 |      57 |
| DOGE-USDT | 1h         |            10 |           140 |  32.23 |      53 |
| ADA-USDT  | 4h         |            25 |            40 |  30.05 |      64 |
| LINK-USDT | 4h         |            45 |            60 |  28.58 |      61 |

## Parameter Instructions
Apply these best params in **Dashboard -> Smart Strategy**.
