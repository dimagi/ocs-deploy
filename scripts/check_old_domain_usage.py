#!/usr/bin/env python3
"""
Search CloudWatch logs for references to the old chatbots.dimagi.com domain.

Supports both the new JSON request logging format (from RequestLoggingMiddleware)
and older plain-text log entries.

Usage:
    # Check Django logs (default)
    ./check_old_domain_usage.py

    # Check specific log group
    ./check_old_domain_usage.py --log-group chatbots-prod-CeleryWorkerLogs

    # Search different time range
    ./check_old_domain_usage.py --days 30

    # Check all log groups
    ./check_old_domain_usage.py --log-group chatbots-prod-DjangoLogs
    ./check_old_domain_usage.py --log-group chatbots-prod-CeleryWorkerLogs
    ./check_old_domain_usage.py --log-group chatbots-prod-CeleryBeatLogs
"""
import argparse
import json
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
    max_wait: int = 30,
) -> list[dict[str, Any]]:
    """Run a CloudWatch Insights query and wait for results."""
    response = client.start_query(
        logGroupName=log_group,
        startTime=start_time,
        endTime=end_time,
        queryString=query_string,
    )
    query_id = response["queryId"]
    print(f"Query ID: {query_id}")

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


def parse_log_entry(row: dict[str, str]) -> dict[str, str]:
    """
    Return a unified log entry dict.

    CloudWatch Logs Insights auto-extracts top-level JSON fields, so for
    request logs emitted by RequestLoggingMiddleware the structured fields
    (host, method, status, path, duration, …) are already present in the row.
    For non-JSON application logs we fall back to the raw @message string.
    """
    entry = dict(row)

    # If CloudWatch didn't auto-extract 'host' (e.g. non-JSON log line),
    # try parsing @message ourselves so downstream code can use the same keys.
    if not entry.get("host"):
        raw = entry.get("@message", "")
        if raw.startswith("{"):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    # Only promote keys not already present
                    for k, v in parsed.items():
                        entry.setdefault(k, str(v))
            except (json.JSONDecodeError, ValueError):
                pass

    return entry


def categorize(entry: dict[str, str]) -> str:
    """Return a category string for the log entry."""
    path = entry.get("path", "")
    message = entry.get("@message", "")
    host = entry.get("host", "")

    # Structured request log: categorise by path / content
    if host:
        if "/start/https:" in path or "/start/https//" in path:
            return "malformed_url"
        if any(
            path.endswith(ext) for ext in (".zip", ".tar", ".rar", ".backup", ".7z")
        ):
            return "scanner"
        if "twilio" in path.lower():
            return "twilio"
        return "request"

    # Plain-text (non-JSON) application log
    if "twilio" in message.lower() and "ErrorUrl" in message:
        return "twilio"
    if "DisallowedHost" in message:
        return "disallowed_host"
    if "/start/https:" in message or "/start/https//" in message:
        return "malformed_url"
    if any(ext in message for ext in (".zip", ".tar", ".rar", ".backup", ".7z")):
        return "scanner"
    return "other"


def format_request_line(entry: dict[str, str]) -> str:
    """Multi-line summary for a structured request log entry."""
    method = entry.get("method", "?")
    status = entry.get("status", "?")
    path = entry.get("path", "?")
    query = entry.get("query", "")
    path_with_qs = f"{path}?{query}" if query else path

    lines = [f"  {method} {path_with_qs}  →  {status}"]

    details = []
    if host := entry.get("host"):
        details.append(("host", host))
    if duration := entry.get("duration"):
        details.append(("duration", f"{duration}ms"))
    if experiment_id := entry.get("experiment_id"):
        details.append(("experiment_id", experiment_id))
    if session_id := entry.get("session_id"):
        details.append(("session_id", session_id))
    if widget_version := entry.get("widget_version"):
        details.append(("widget_version", widget_version))
    if request_id := entry.get("request_id"):
        details.append(("request_id", request_id))

    if details:
        lines.append("  " + "  ".join(f"{k}={v}" for k, v in details))

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Search CloudWatch logs for old domain usage"
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
        "--domain",
        default="chatbots.dimagi.com",
        help="Domain to search for (default: chatbots.dimagi.com)",
    )
    parser.add_argument(
        "--limit", type=int, default=200, help="Max results (default: 200)"
    )
    args = parser.parse_args()

    session = boto3.Session(profile_name=args.profile)
    client = session.client("logs")

    end_time = int(time.time())
    start_time = end_time - (args.days * 24 * 60 * 60)

    print(f"Searching {args.log_group}")
    print(
        f"Time range: {datetime.fromtimestamp(start_time)} to {datetime.fromtimestamp(end_time)}"
    )
    print(f"Looking for: {args.domain}\n")

    # CloudWatch Logs Insights auto-extracts top-level JSON fields, so
    # 'host', 'method', 'status', 'path', 'duration' etc. are queryable
    # directly for logs produced by RequestLoggingMiddleware.
    # We keep '@message like' as a fallback to catch non-JSON log lines.
    query_string = f"""
    fields @timestamp, @message, host, method, status, path, duration, request_id, experiment_id, session_id, widget_version, query
    | filter host like /{args.domain}/ or @message like /{args.domain}/
    | sort @timestamp desc
    | limit {args.limit}
    """

    try:
        results = run_insights_query(
            client, args.log_group, query_string, start_time, end_time
        )

        if not results:
            print(
                f"✓ No references to {args.domain} found in the past {args.days} days"
            )
            return 0

        print(f"Found {len(results)} log entries with '{args.domain}':\n")
        print("=" * 100)

        buckets: dict[str, list[tuple[str, dict]]] = {
            "twilio": [],
            "disallowed_host": [],
            "malformed_url": [],
            "scanner": [],
            "request": [],
            "other": [],
        }

        for raw_row in results:
            row = format_result(raw_row)
            entry = parse_log_entry(row)
            ts = entry.get("@timestamp", "")
            cat = categorize(entry)
            buckets[cat].append((ts, entry))

        # ── Twilio ────────────────────────────────────────────────────────────
        if buckets["twilio"]:
            print(f"\n🚨 TWILIO WEBHOOK REFERENCES ({len(buckets['twilio'])}):")
            print("-" * 100)
            for ts, entry in sorted(
                buckets["twilio"], key=lambda x: x[0], reverse=True
            )[:5]:
                print(f"\n{ts}")
                if entry.get("host"):
                    print(format_request_line(entry))
                else:
                    msg = entry.get("@message", "")
                    try:
                        if "'ErrorUrl':" in msg:
                            url = msg.split("'ErrorUrl': '")[1].split("'")[0]
                            print(f"  ErrorUrl: {url}")
                        if "CallSid" in msg:
                            call_sid = msg.split("'CallSid': '")[1].split("'")[0]
                            print(f"  CallSid: {call_sid}")
                    except IndexError:
                        pass
                    print(f"  {msg[:200]}...")

        # ── DisallowedHost (plain-text logs only) ─────────────────────────────
        if buckets["disallowed_host"]:
            print(f"\n⚠️  DISALLOWED HOST ERRORS ({len(buckets['disallowed_host'])}):")
            print("-" * 100)
            for ts, entry in sorted(
                buckets["disallowed_host"], key=lambda x: x[0], reverse=True
            )[:5]:
                print(f"{ts}: {entry.get('@message', '')[:150]}")

        # ── Malformed URLs ────────────────────────────────────────────────────
        if buckets["malformed_url"]:
            print(f"\n🐛 MALFORMED URLs ({len(buckets['malformed_url'])}):")
            print("-" * 100)
            for ts, entry in sorted(
                buckets["malformed_url"], key=lambda x: x[0], reverse=True
            )[:5]:
                print(f"\n{ts}")
                if entry.get("host"):
                    print(format_request_line(entry))
                else:
                    print(f"  {entry.get('@message', '')[:150]}")

        # ── Scanner / attacker requests ───────────────────────────────────────
        if buckets["scanner"]:
            print(
                f"\n🤖 SCANNER/ATTACKER REQUESTS ({len(buckets['scanner'])}) - Can ignore"
            )
            print("-" * 100)
            print("  (Not showing details - just automated scanners)")

        # ── Regular requests with old domain in Host header ───────────────────
        if buckets["request"]:
            print(f"\n🌐 REQUESTS WITH OLD HOST HEADER ({len(buckets['request'])}):")
            print("-" * 100)
            for ts, entry in sorted(
                buckets["request"], key=lambda x: x[0], reverse=True
            )[:10]:
                print(f"{ts}{format_request_line(entry)}")

        # ── Other ─────────────────────────────────────────────────────────────
        if buckets["other"]:
            print(f"\n📋 OTHER REFERENCES ({len(buckets['other'])}):")
            print("-" * 100)
            for ts, entry in sorted(buckets["other"], key=lambda x: x[0], reverse=True)[
                :10
            ]:
                print(f"\n{ts}")
                if entry.get("host"):
                    print(format_request_line(entry))
                else:
                    print(f"  {entry.get('@message', '')[:200]}...")

        print("\n" + "=" * 100)
        print(f"\nTotal: {len(results)} entries")

        print("\n📊 SUMMARY:")
        if buckets["twilio"]:
            print(
                f"  ❗ {len(buckets['twilio'])} Twilio webhook references - UPDATE REQUIRED"
            )
        if buckets["disallowed_host"]:
            print(f"  ⚠️  {len(buckets['disallowed_host'])} DisallowedHost errors")
        if buckets["malformed_url"]:
            print(f"  🐛 {len(buckets['malformed_url'])} malformed URL bugs")
        if buckets["scanner"]:
            print(f"  🤖 {len(buckets['scanner'])} scanner requests (ignore)")
        if buckets["request"]:
            print(f"  🌐 {len(buckets['request'])} regular requests to old domain")
        if buckets["other"]:
            print(f"  📋 {len(buckets['other'])} other references")

    except Exception as e:
        print(f"Error: {e}")
        return 1

    return 0


if __name__ == "__main__":
    exit(main())
