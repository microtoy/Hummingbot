# Strategy Optimization Report: Discovery

**Date**: 2026-01-03 13:09
**Analysis Window**: Last 360 days
**Resolution**: 1m (High Precision)

## Technical Summary
- **Turbo Mode**: Enabled ⚡
- **Concurrency**: 2 Workers | 250 Batch Size
- **Simulations**: 5000 successful / 5000 sent (100.0% success)
- **Total Time**: 23m 58s
- **Efficiency**: 0.29s per successful sim

## Top Performers (Holy Grails)
> **Criteria**: PnL > 15% , Max Drawdown < 20%

| Pair      | Config                                |   PnL |   Drawdown |   Sharpe |
|:----------|:--------------------------------------|------:|-----------:|---------:|
| DOGE-USDT | Fast 35/Slow 90/1h (SL 0.05/TP 0.05)  | 52.85 |     -11.86 |     0.70 |
| SOL-USDT  | Fast 20/Slow 70/1h (SL 0.04/TP 0.15)  | 52.58 |     -14.53 |     1.94 |
| DOGE-USDT | Fast 15/Slow 100/1h (SL 0.07/TP 0.05) | 52.43 |     -19.75 |     1.11 |
| SOL-USDT  | Fast 25/Slow 40/1h (SL 0.1/TP 0.15)   | 52.22 |     -17.80 |     0.24 |
| DOGE-USDT | Fast 35/Slow 90/1h (SL 0.07/TP 0.08)  | 52.10 |     -14.76 |     0.74 |
| DOGE-USDT | Fast 35/Slow 90/1h (SL 0.07/TP 0.08)  | 52.10 |     -14.76 |     0.74 |
| SOL-USDT  | Fast 15/Slow 30/1h (SL 0.05/TP 0.08)  | 51.44 |     -18.97 |     1.35 |
| LINK-USDT | Fast 5/Slow 110/1h (SL 0.01/TP 0.2)   | 50.69 |     -14.38 |     0.52 |
| DOGE-USDT | Fast 10/Slow 150/1h (SL 0.07/TP 0.08) | 49.09 |     -12.59 |     1.14 |
| DOGE-USDT | Fast 55/Slow 130/1h (SL 0.03/TP 0.2)  | 47.35 |     -18.21 |     0.75 |

## Sweet Spot Analysis (Robust Parameter Clusters)
> **Why this matters**: Individual peaks are often overfitting. We look for 'Sweet Spots'—ranges of parameters that perform consistently well across multiple tests.

### Top MA Range Clusters
| Pair      | Interval   |   Fast_Bucket |   Slow_Bucket |   mean |   count |
|:----------|:-----------|--------------:|--------------:|-------:|--------:|
| SOL-USDT  | 1h         |            20 |            60 |  49.45 |       2 |
| DOGE-USDT | 1h         |            10 |           140 |  46.04 |       2 |
| DOGE-USDT | 1h         |            35 |            80 |  45.98 |       4 |
| DOGE-USDT | 1h         |             5 |           140 |  33.57 |       2 |
| LINK-USDT | 4h         |            45 |            60 |  30.96 |       3 |
| DOGE-USDT | 1h         |             5 |            40 |  30.92 |       3 |
| LINK-USDT | 1h         |             5 |           100 |  29.48 |       2 |
| ADA-USDT  | 1h         |            10 |           140 |  28.81 |       2 |
| AVAX-USDT | 1h         |            55 |           140 |  28.59 |       7 |
| DOGE-USDT | 1h         |            35 |           100 |  27.92 |       5 |

## Parameter Instructions
Apply these best params in **Dashboard -> Smart Strategy**.
