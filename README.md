# morning-brief

Collects market closes and headlines every weekday at 06:15 JST into `data/latest.json`.
A Claude scheduled task reads that file at ~07:00 JST, writes the causal chains and republishes the brief page.

Sources: Yahoo Finance (via yfinance), FRED (HY spread, St. Louis Fed stress index), Ministry of Finance (JGB 10Y), RSS from NHK, BBC and Folha.

## Setup

1. Create the repo on GitHub and push these files.
2. Actions tab → `collect` → **Run workflow** once to test.
3. Check that `data/latest.json` appears with today's numbers.
