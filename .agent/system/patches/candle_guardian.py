import pandas as pd
import numpy as np
import json
import os
import time
from pathlib import Path
from typing import Dict, List, Optional
import hummingbot

class CandleGuardian:
    def __init__(self, cache_dir: Optional[str] = None):
        self.cache_dir = Path(cache_dir or os.path.join(hummingbot.data_path(), "candles"))
        self.interval_seconds = {
            "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
            "1h": 3600, "4h": 14400, "1d": 86400
        }

    def _get_sidecar_path(self, csv_file: Path) -> Path:
        return csv_file.with_suffix(".health.json")

    def run_health_scan(self, filename: str) -> Dict:
        """Runs a full health scan on a specific CSV file."""
        csv_path = self.cache_dir / filename
        if not csv_path.exists():
            return {"error": "File not found"}

        try:
            # 1. Basic Stats
            file_stats = csv_path.stat()
            df = pd.read_csv(csv_path)
            
            if df.empty:
                return {"error": "File is empty"}

            df = df.sort_values("timestamp")
            ts = df["timestamp"].values
            
            # Extract interval from filename
            parts = csv_path.stem.split("_")
            interval_str = parts[-1]
            interval_sec = self.interval_seconds.get(interval_str, 60)

            # 2. Duplicate Check
            duplicates = df.duplicated(subset=["timestamp"]).sum()
            
            # 3. Gap Detection (Vectorized)
            diffs = ts[1:] - ts[:-1]
            gap_indices = (diffs > interval_sec * 1.5).nonzero()[0]
            gaps = []
            for idx in gap_indices:
                gaps.append({
                    "start": int(ts[idx]),
                    "end": int(ts[idx+1]),
                    "missing_seconds": int(ts[idx+1] - ts[idx] - interval_sec)
                })

            # 4. Anomaly Detection (Phase 2 preview)
            # Zombie candles: price hasn't moved but volume exists
            # We flag if high-low == 0 for more than 10 consecutive ticks
            price_delta = (df['high'] - df['low']).values
            is_stale = (price_delta == 0).astype(int)
            # Simple rolling sum for stale check
            stale_score = np.convolve(is_stale, np.ones(10, dtype=int), 'valid')
            zombie_periods = (stale_score >= 10).sum()

            # 5. Metadata Preparation
            health_metadata = {
                "file": filename,
                "last_scan": int(time.time()),
                "rows": len(df),
                "start_ts": int(ts[0]),
                "end_ts": int(ts[-1]),
                "duplicates": int(duplicates),
                "gaps_count": len(gaps),
                "gaps": gaps,
                "zombie_ticks": int(zombie_periods),
                "health_score": self._calculate_score(len(df), len(gaps), duplicates, zombie_periods)
            }

            # Save Sidecar
            sidecar_path = self._get_sidecar_path(csv_path)
            with open(sidecar_path, 'w') as f:
                json.dump(health_metadata, f, indent=4)

            return health_metadata

        except Exception as e:
            return {"error": str(e)}

    def _calculate_score(self, rows: int, gaps: int, duplicates: int, zombies: int) -> int:
        """A simple health scoring algorithm (0-100)."""
        if rows == 0: return 0
        score = 100
        # Deduct for gaps (1 point per gap, capped at 40)
        score -= min(40, gaps * 2)
        # Deduct for duplicates (harsh)
        if duplicates > 0: score -= 10
        # Deduct for zombies
        score -= min(20, zombies // 10)
        return max(0, score)

    def get_health_summary(self, filename: str) -> Optional[Dict]:
        """Loads health metadata if it exists."""
        sidecar_path = self._get_sidecar_path(self.cache_dir / filename)
        if sidecar_path.exists():
            with open(sidecar_path, 'r') as f:
                return json.load(f)
        return None
