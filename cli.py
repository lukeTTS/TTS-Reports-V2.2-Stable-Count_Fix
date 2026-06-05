import argparse, json
from report_core import generate_report


def main():
    parser = argparse.ArgumentParser(description="Generate TTS PRO Reports from .padfx, .apx or .xlsx project files")
    parser.add_argument("project_file")
    parser.add_argument("output")
    parser.add_argument("--general", help="Optional JSON general data file")
    parser.add_argument("--report-type", choices=["pro", "basic", "retest"], default="pro")
    parser.add_argument("--no-asset-cards", action="store_true")
    parser.add_argument("--filter-mode", choices=["latest", "range", "all"], default="latest")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    args = parser.parse_args()
    general = {}
    if args.general:
        with open(args.general, "r", encoding="utf-8") as f:
            general = json.load(f)
    out = generate_report(args.project_file, args.output, general, include_asset_cards=not args.no_asset_cards, report_type=args.report_type, filter_mode=args.filter_mode, start_date=args.start_date, end_date=args.end_date)
    print(out)

if __name__ == "__main__":
    main()
