#!/usr/bin/env python3
"""
Produce a chat widget version usage report from the Django request logs.

Counts requests grouped by the 'widget_version' field emitted by
RequestLoggingMiddleware, aggregated server-side via CloudWatch Logs Insights.

Usage:
    # Report for the past 14 days, CSV to stdout
    ./widget_version_report.py

    # Search different time range
    ./widget_version_report.py --days 30

    # Write CSV to a file
    ./widget_version_report.py --output widget_versions.csv
"""
import argparse
import csv
import sys
import time
from datetime import datetime
from typing import Any

import boto3


def run_insights_query(
    client,
    log_group: str,
    query_string: str,
    start_time: int,
    end_time: int,
    max_wait: int = 60,
) -> list[dict[str, Any]]:
    """Run a CloudWatch Insights query and wait for results."""
    response = client.start_query(
        logGroupName=log_group,
        startTime=start_time,
        endTime=end_time,
        queryString=query_string,
    )
    query_id = response["queryId"]
    print(f"Query ID: {query_id}", file=sys.stderr)

    waited = 0
    while waited < max_wait:
        result = client.get_query_results(queryId=query_id)
        status = result["status"]

        if status == "Complete":
            return result["results"]
        elif status == "Failed":
            raise Exception(f"Query failed: {result}")

        time.sleep(2)
        waited += 2

    raise Exception(f"Query timed out after {max_wait} seconds")


def format_result(result: list[dict[str, str]]) -> dict[str, str]:
    """Convert query result row to a flat dict of field->value."""
    return {item["field"]: item["value"] for item in result}


def main():
    parser = argparse.ArgumentParser(
        description="Report chat widget version usage from Django request logs"
    )
    parser.add_argument(
        "--profile", default="ocs-prod", help="AWS profile (default: ocs-prod)"
    )
    parser.add_argument(
        "--log-group",
        default="chatbots-prod-DjangoLogs",
        help="Log group name (default: chatbots-prod-DjangoLogs)",
    )
    parser.add_argument(
        "--days", type=int, default=14, help="Days to search back (default: 14)"
    )
    parser.add_argument(
        "--output",
        help="CSV output file path (default: stdout)",
    )
    args = parser.parse_args()

    session = boto3.Session(profile_name=args.profile)
    client = session.client("logs")

    end_time = int(time.time())
    start_time = end_time - (args.days * 24 * 60 * 60)

    print(f"Searching {args.log_group}", file=sys.stderr)
    print(
        f"Time range: {datetime.fromtimestamp(start_time)} to {datetime.fromtimestamp(end_time)}",
        file=sys.stderr,
    )

    # CloudWatch Logs Insights auto-extracts top-level JSON fields from logs
    # produced by RequestLoggingMiddleware, so 'widget_version' is queryable
    # directly and the aggregation happens server-side.
    query_string = """
    fields widget_version
    | filter ispresent(widget_version)
    | stats count(*) as requests by widget_version
    | sort requests desc
    """

    try:
        results = run_insights_query(
            client, args.log_group, query_string, start_time, end_time
        )
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if not results:
        print(
            f"No requests with widget_version found in the past {args.days} days",
            file=sys.stderr,
        )
        return 0

    rows = [format_result(raw_row) for raw_row in results]

    out = open(args.output, "w", newline="") if args.output else sys.stdout
    try:
        writer = csv.writer(out)
        writer.writerow(["widget_version", "requests"])
        for row in rows:
            writer.writerow([row.get("widget_version", ""), row.get("requests", "")])
    finally:
        if args.output:
            out.close()
            print(f"Wrote {len(rows)} rows to {args.output}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    exit(main())
