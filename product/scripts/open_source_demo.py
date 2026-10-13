"""Small offline example using the product's actual inference modules and fictional inputs."""
from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from bidking_lab.inference.display import enumerate_candidates, parse_reading
from bidking_lab.inference.mean_multiset_support import MeanSupportCandidate, solve_repeat_allowed_mean_support

def main():
    support = solve_repeat_allowed_mean_support(
        [MeanSupportCandidate(1, 3, 10, 1), MeanSupportCandidate(2, 4, 20, 1)],
        total_lower=30, total_upper=30, item_count=2,
    )
    report = {
        'synthetic': True,
        'network_used': False,
        'reading': '2.90',
        'compatible_cells_and_counts': enumerate_candidates(parse_reading('2.90'), max_count=20, max_total_cells=60),
        'support_status': support.status,
        'signatures': [s.as_dict() for s in support.signatures],
    }
    print(json.dumps(report, indent=2))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
