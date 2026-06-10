#!/usr/bin/env python3
import json
import os
import sys
import glob
import argparse
from datetime import datetime

BOLD = "\033[1m" if sys.stdout.isatty() else ""
GREEN = "\033[32m" if sys.stdout.isatty() else ""
BLUE = "\033[34m" if sys.stdout.isatty() else ""
CYAN = "\033[36m" if sys.stdout.isatty() else ""
YELLOW = "\033[33m" if sys.stdout.isatty() else ""
RED = "\033[31m" if sys.stdout.isatty() else ""
RESET = "\033[0m" if sys.stdout.isatty() else ""


def format_time(ts_str):
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ts_str


def parse_transcript(file_path, truncate=True, raw=False):
    if not os.path.exists(file_path):
        print(f"{RED}Error: File not found at {file_path}{RESET}")
        return

    print(f"\n{BOLD}{CYAN}=== PARSING TRANSCRIPT: {os.path.basename(file_path)} ==={RESET}")

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            for idx, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError as e:
                    print(f"{YELLOW}Warning: Skipping invalid JSON on line {idx + 1}: {e}{RESET}")
                    continue

                if raw:
                    print(f"\n{BOLD}{GREEN}Step #{idx + 1} | Raw JSON:{RESET}")
                    print(json.dumps(entry, indent=2))
                    print(f"{GREEN}" + "=" * 80 + f"{RESET}")
                    continue

                timestamp = entry.get("timestamp", "Unknown Time")
                request = entry.get("request", {})
                response = entry.get("response", {})
                model = request.get("model", "unknown-model")
                messages = request.get("messages", [])

                print(
                    f"\n{BOLD}{GREEN}Step #{idx + 1} | {format_time(timestamp)} | Model: {model}{RESET}"
                )
                print(f"{GREEN}" + "-" * 80 + f"{RESET}")

                last_assistant_idx = -1
                for i in range(len(messages) - 1, -1, -1):
                    if messages[i].get("role") == "assistant":
                        last_assistant_idx = i
                        break

                start_idx = last_assistant_idx + 1
                new_messages = messages[start_idx:]

                if new_messages:
                    for msg in new_messages:
                        role = msg.get("role", "").upper()
                        content = msg.get("content", "")
                        name = msg.get("name", "")

                        display_content = content
                        if truncate and len(content) > 1000:
                            display_content = (
                                content[:1000]
                                + f"\n\n{YELLOW}... [TRUNCATED {len(content) - 1000} CHARS. Use --no-truncate to see all] ...{RESET}"
                            )

                        if role == "USER":
                            print(f"{BOLD}[USER INPUT]:{RESET} {display_content}")
                        elif role == "TOOL":
                            print(
                                f"{BOLD}[TOOL RESPONSE] (Tool: {name}):{RESET}\n{display_content}"
                            )
                        elif role == "SYSTEM":
                            if idx == 0:
                                print(f"{BOLD}[SYSTEM PROMPT]:{RESET} {display_content}")
                        else:
                            print(f"{BOLD}[{role}]:{RESET} {display_content}")

                choices = response.get("choices", [])
                if choices:
                    choice = choices[0]
                    message = choice.get("message", {})

                    reasoning = message.get("reasoning_content") or message.get("reasoning")
                    if not reasoning and hasattr(response, "get"):
                        reasoning = response.get("model_extra", {}).get("reasoning_content")

                    content = message.get("content")
                    tool_calls = message.get("tool_calls")

                    if reasoning:
                        print(f"\n{BOLD}{BLUE}[THINKING/REASONING]:{RESET}")
                        print(f"{BLUE}{reasoning.strip()}{RESET}")

                    if content:
                        print(f"\n{BOLD}[ASSISTANT RESPONSE]:{RESET}\n{content.strip()}")

                    if tool_calls:
                        print(f"\n{BOLD}{YELLOW}[TOOL CALLS INITIATED]:{RESET}")
                        for tc in tool_calls:
                            tc_id = tc.get("id", "N/A")
                            func = tc.get("function", {})
                            name = func.get("name", "unknown")
                            args = func.get("arguments", "{}")
                            print(f"  - {BOLD}Tool:{RESET} {name} (ID: {tc_id})")
                            try:
                                parsed_args = json.loads(args)
                                pretty_args = json.dumps(parsed_args, indent=4)
                                pretty_args = "\n".join(
                                    "      " + line for line in pretty_args.split("\n")
                                )
                                print(f"    {BOLD}Arguments:{RESET}\n{pretty_args}")
                            except Exception:
                                print(f"    {BOLD}Arguments (raw):{RESET} {args}")
                elif "error" in response:
                    print(
                        f"\n{RED}[API ERROR]:{RESET} {json.dumps(response.get('error'), indent=2)}"
                    )
                else:
                    print(f"\n{YELLOW}[RAW RESPONSE]:{RESET} {json.dumps(response)[:300]}...")

                print(f"{GREEN}" + "=" * 80 + f"{RESET}")
    except Exception as e:
        print(f"{RED}Error parsing file: {e}{RESET}")


def main():
    parser = argparse.ArgumentParser(
        description="Parse agent life transcripts to show reasoning and tool calls."
    )
    parser.add_argument("file", nargs="?", help="Path to the JSONL transcript file to parse.")
    parser.add_argument(
        "--latest",
        action="store_true",
        help="Parse the latest archived transcript in the tombstones directory.",
    )
    parser.add_argument("--active", action="store_true", help="Parse the active transcript log.")
    parser.add_argument(
        "--no-truncate", action="store_true", help="Do not truncate long tool/user content."
    )
    parser.add_argument("--raw", action="store_true", help="Print the raw JSON for each step.")
    args = parser.parse_args()

    active_log = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "agent_life_transcript.jsonl"
    )
    tombstone_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tombstones")

    target_file = None

    if args.file:
        target_file = args.file
    elif args.latest:
        archive_files = glob.glob(os.path.join(tombstone_dir, "transcript_*.jsonl"))
        if archive_files:
            target_file = max(archive_files, key=os.path.getmtime)
        else:
            print(
                f"{YELLOW}No archived logs found in tombstones directory. Checking active log...{RESET}"
            )
            target_file = active_log
    elif args.active:
        target_file = active_log
    else:
        is_interactive = sys.stdin.isatty() and sys.stdout.isatty()

        archive_files = sorted(
            glob.glob(os.path.join(tombstone_dir, "transcript_*.jsonl")),
            key=os.path.getmtime,
            reverse=True,
        )
        choices = []
        if os.path.exists(active_log):
            choices.append(("Active transcript log", active_log))

        for f in archive_files[:5]:
            choices.append((f"Archived log: {os.path.basename(f)}", f))

        if not choices:
            print(f"{RED}No transcript logs found in workspace.{RESET}")
            sys.exit(1)

        if not is_interactive:
            target_file = choices[0][1]
        else:
            print(f"{BOLD}Select a transcript to parse:{RESET}")
            for i, (desc, _) in enumerate(choices):
                print(f"  [{i}] {desc}")

            try:
                val = input(f"\nEnter choice [0-{len(choices) - 1}] (default 0): ").strip()
                idx = int(val) if val else 0
                if idx < 0 or idx >= len(choices):
                    print(f"{RED}Invalid index. Defaulting to 0.{RESET}")
                    idx = 0
                target_file = choices[idx][1]
            except (KeyboardInterrupt, EOFError):
                print("\nAborted.")
                sys.exit(0)
            except ValueError:
                print(f"{YELLOW}Invalid input. Defaulting to index 0.{RESET}")
                target_file = choices[0][1]

    parse_transcript(target_file, truncate=not args.no_truncate, raw=args.raw)


if __name__ == "__main__":
    main()
