import os
import glob
from datetime import datetime

def scan_coverage():
    candle_files = glob.glob("/Users/microtoy/Documents/QuantWin/deploy/data/candles/*_1m.csv")
    tokens = []
    
    # 2021-01-01
    START_2021 = 1609459200
    # 2025-01-01
    END_2025 = 1735689600
    
    print(f"{'Token':<15} | {'Min Year':<10} | {'Max Year':<10} | {'Status'}")
    print("-" * 65)
    
    for fpath in sorted(candle_files):
        fname = os.path.basename(fpath)
        token = fname.replace("binance_", "").replace("_1m.csv", "")
        
        # Quick check for coverage without reading the whole file
        try:
            with open(fpath, "r") as f:
                header = f.readline()
                first_row = f.readline()
                if not first_row: continue
                min_ts = float(first_row.split(",")[0])
            
            # Use tail logic for max_ts
            tail_out = os.popen(f"tail -n 1 {fpath}").read().strip()
            if not tail_out: continue
            max_ts = float(tail_out.split(",")[0])
            
            min_year = datetime.fromtimestamp(min_ts).year
            max_year = datetime.fromtimestamp(max_ts).year
            
            status = "❌ Incomplete"
            # We want tokens that cover at least 2021 to 2025
            if min_ts <= START_2021 and max_ts >= END_2025:
                # Also check row count to ensure no massive gaps
                # (rough heuristic: 1.47M for 9 years is bad, need ~525k per year)
                line_count = int(os.popen(f"wc -l < {fpath}").read().strip())
                years_covered = (max_ts - min_ts) / (365*24*3600)
                expected_lines = years_covered * 525600
                density = line_count / expected_lines if expected_lines > 0 else 0
                
                if density > 0.8:
                    status = "✅ DISCOVERABLE"
                    tokens.append(token)
                else:
                    status = f"⚠️ Sparse ({int(density*100)}%)"
            
            print(f"{token:<15} | {min_year:<10} | {max_year:<10} | {status}")
        except Exception as e:
            # print(f"Error scanning {token}: {e}")
            continue
            
    print("\n" + "="*65)
    print(f"Discoverable Tokens ({len(tokens)}): {', '.join(tokens)}")
    print("="*65)

if __name__ == "__main__":
    scan_coverage()
