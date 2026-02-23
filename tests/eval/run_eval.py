"""CLI entry point for eval runner."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .runner import EvalRunner


def main():
    parser = argparse.ArgumentParser(description="Run OpenEMR agent eval suite")
    parser.add_argument("--url", default="http://localhost:8000", help="Agent API URL")
    parser.add_argument("--category", choices=["happy_path", "edge_case", "adversarial", "output_quality"], help="Run only this category")
    parser.add_argument("--case-id", help="Run a single case by ID")
    parser.add_argument("--output", help="Save results to JSON file")
    args = parser.parse_args()

    runner = EvalRunner(agent_url=args.url)

    async def run():
        if args.case_id:
            case = next((c for c in runner.dataset if c["id"] == args.case_id), None)
            if not case:
                print(f"Case {args.case_id} not found")
                sys.exit(1)
            result = await runner.run_case(case)
            print(json.dumps(result.model_dump(), indent=2))
            return

        if args.category:
            results = await runner.run_all(category=args.category)
            passed = sum(1 for r in results if r.passed)
            print(f"\n{args.category}: {passed}/{len(results)} passed")
            if args.output:
                Path(args.output).write_text(json.dumps([r.model_dump() for r in results], indent=2))
            return

        report = await runner.run_suite()
        print(f"\n{report.summary}")
        if args.output:
            Path(args.output).write_text(json.dumps(report.model_dump(), indent=2))

    asyncio.run(run())


if __name__ == "__main__":
    main()
