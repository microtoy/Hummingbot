from datetime import datetime
import time

def assess_gaps(target_start, target_end, file_cache):
    # Identify Gaps
    gaps = []
    if not file_cache:
        # No cache at all: full range is a gap
        gaps.append((target_start, target_end))
    else:
        c_start = int(file_cache["start"])
        c_end = int(file_cache["end"])
        
        # Check for prefix gap
        if target_start < c_start - 3600: # 1h tolerance
            gaps.append((target_start, c_start))
        
        # Check for suffix gap
        effective_now = int(time.time()) - 120
        real_target_end = min(target_end, effective_now)
        
        if real_target_end > c_end + 300: # 5m tolerance
            gaps.append((c_end, real_target_end))
    return gaps

# User Data from screenshot/description
# Cache: From 2024-08-30 06:20 To 2025-12-31 23:59
c_start = int(datetime(2024, 8, 30, 6, 20).timestamp())
c_end = int(datetime(2025, 12, 31, 23, 59).timestamp())
file_cache = {"start": c_start, "end": c_end, "count": 703780}

# Target: From 2024-12-05 To 2025-12-31
target_start = int(datetime(2024, 12, 5, 0, 0).timestamp())
target_end = int(datetime(2025, 12, 31, 23, 59).timestamp())

gaps = assess_gaps(target_start, target_end, file_cache)
print(f"Test 1 (Normal Case): Gaps found: {gaps}")

# Scenario where now < target_end
# (In the user case, now is 2026-01-01, so real_target_end = target_end)

# Test 2: What if target_end is in the future?
future_target_end = int(datetime(2026, 12, 31).timestamp())
gaps_future = assess_gaps(target_start, future_target_end, file_cache)
print(f"Test 2 (Future Target): Gaps found: {len(gaps_future)}")
if gaps_future:
    gs, ge = gaps_future[0]
    print(f"  Gap: {datetime.fromtimestamp(gs)} -> {datetime.fromtimestamp(ge)}")
