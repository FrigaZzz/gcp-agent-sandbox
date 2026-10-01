import csv
import json
from collections import defaultdict
from pathlib import Path


def process_transactions():
    input_file = Path("transactions.csv")
    with open(input_file, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    dept_totals = defaultdict(float)
    total_revenue = 0.0

    for r in rows:
        amount = float(r["amount"])
        dept = r["department"]
        dept_totals[dept] += amount
        total_revenue += amount

    summary = {
        "transaction_count": len(rows),
        "total_revenue": round(total_revenue, 2),
        "by_department": dict(dept_totals),
        "status": "ANALYSIS_COMPLETE",
    }

    output_path = Path("analysis_summary.json")
    output_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Summary generated: {summary}")


if __name__ == "__main__":
    process_transactions()
