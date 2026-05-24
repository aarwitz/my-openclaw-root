#!/usr/bin/env python3
import argparse
import glob
import json
import re
from pathlib import Path


def load_session_file(base: Path, agent: str, sender: str) -> Path:
    sessions_index = base / "agents" / agent / "sessions" / "sessions.json"
    with sessions_index.open("r", encoding="utf-8") as f:
        data = json.load(f)

    key_suffix = f"telegram:direct:{sender}"
    for key, value in data.items():
        if key_suffix in key:
            session_file = value.get("sessionFile")
            if session_file:
                return Path(session_file)

    raise RuntimeError(f"No direct Telegram session found for {agent=} {sender=}")


def iter_jsonl(path: Path):
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def find_latest_rollout_file(base: Path, agent: str) -> Path | None:
    pattern = str(base / "agents" / agent / "agent" / "codex-home" / "sessions" / "**" / "rollout-*.jsonl")
    matches = glob.glob(pattern, recursive=True)
    if not matches:
        return None
    matches.sort(key=lambda p: Path(p).stat().st_mtime, reverse=True)
    return Path(matches[0])


def find_rollout_files(base: Path, agent: str):
    pattern = str(base / "agents" / agent / "agent" / "codex-home" / "sessions" / "**" / "rollout-*.jsonl")
    matches = [Path(p) for p in glob.glob(pattern, recursive=True)]
    matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return matches


def extract_rollout_stream_chunks(path: Path):
    chunks = []
    for row in iter_jsonl(path):
        if not isinstance(row, dict):
            continue
        if row.get("type") != "response_item":
            continue
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue
        if payload.get("type") != "message" or payload.get("role") != "assistant":
            continue

        content = payload.get("content")
        if not isinstance(content, list):
            continue

        ts = row.get("timestamp")
        for item in content:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type not in ("output_text", "text"):
                continue
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                chunks.append({
                    "timestamp": ts,
                    "text": text.strip()
                })
    return chunks


def iter_rollout_message_text(path: Path):
    for row in iter_jsonl(path):
        if not isinstance(row, dict):
            continue
        if row.get("type") != "response_item":
            continue
        payload = row.get("payload")
        if not isinstance(payload, dict) or payload.get("type") != "message":
            continue
        role = payload.get("role", "unknown")
        ts = row.get("timestamp")
        content = payload.get("content")
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type not in ("output_text", "input_text", "text"):
                continue
            text = item.get("text")
            if isinstance(text, str) and text:
                yield {
                    "timestamp": ts,
                    "role": role,
                    "text": text,
                    "item_type": item_type
                }


def scan_session_for_term(rows, pattern):
    matches = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue
        message = row.get("message")
        if not isinstance(message, dict):
            continue
        role = message.get("role", "unknown")
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, dict):
                continue
            candidate_texts = []
            for key in ("text", "content", "output"):
                value = item.get(key)
                if isinstance(value, str) and value:
                    candidate_texts.append(value)
            for text in candidate_texts:
                if pattern.search(text):
                    matches.append({
                        "row": index,
                        "role": role,
                        "item_type": item.get("type", "item"),
                        "text": text
                    })
    return matches


def scan_trajectory_for_term(path: Path, pattern):
    matches = []
    for index, row in enumerate(iter_jsonl(path), start=1):
        serialized = json.dumps(row, ensure_ascii=False)
        if pattern.search(serialized):
            matches.append({
                "row": index,
                "type": row.get("type") if isinstance(row, dict) else "unknown",
                "text": serialized
            })
    return matches


def short_snippet(text: str, max_len: int = 220) -> str:
    compact = " ".join(text.split())
    if len(compact) <= max_len:
        return compact
    return compact[: max_len - 3] + "..."


def extract_text(msg_obj) -> str:
    if not isinstance(msg_obj, dict):
        return ""
    message = msg_obj.get("message", {})
    role = message.get("role")
    content = message.get("content", [])

    texts = []
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text" and isinstance(item.get("text"), str):
                    texts.append(item["text"])
                elif item.get("type") == "toolResult":
                    text = item.get("text") or item.get("content")
                    if isinstance(text, str):
                        texts.append(text)

    if not texts:
        return ""

    body = "\n\n".join(texts).strip()
    if not body:
        return ""

    return f"[{role}] {body}"


def extract_full_message(msg_obj) -> str:
    if not isinstance(msg_obj, dict):
        return ""

    message = msg_obj.get("message", {})
    role = message.get("role", "unknown")
    parts = []

    content = message.get("content", [])
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type", "item")
            if item_type == "text" and isinstance(item.get("text"), str):
                parts.append(item["text"])
            elif item_type == "toolCall":
                tool_name = item.get("name", "toolCall")
                arguments = item.get("arguments")
                if isinstance(arguments, (dict, list)):
                    rendered = json.dumps(arguments, ensure_ascii=False)
                else:
                    rendered = str(arguments) if arguments is not None else ""
                if rendered:
                    parts.append(f"[toolCall:{tool_name}] {rendered}")
                else:
                    parts.append(f"[toolCall:{tool_name}]")
            elif item_type == "toolResult":
                tool_name = item.get("name", "toolResult")
                text = item.get("text") or item.get("content") or item.get("output")
                if isinstance(text, str) and text.strip():
                    parts.append(f"[toolResult:{tool_name}] {text.strip()}")
                else:
                    parts.append(f"[toolResult:{tool_name}] {json.dumps(item, ensure_ascii=False)}")

    if not parts:
        return ""

    return f"[{role}] " + "\n".join(parts).strip()


def find_last_user_index(rows):
    for index in range(len(rows) - 1, -1, -1):
        row = rows[index]
        if not isinstance(row, dict):
            continue
        message = row.get("message", {})
        if isinstance(message, dict) and message.get("role") == "user":
            return index
    return max(0, len(rows) - 1)


def main():
    parser = argparse.ArgumentParser(description="Show latest Telegram direct turn from local OpenClaw session logs.")
    parser.add_argument("--base", default="/home/aaron/.openclaw", help="OpenClaw base directory")
    parser.add_argument("--agent", default="dwight", help="Agent id (default: dwight)")
    parser.add_argument("--sender", default="6043080629", help="Telegram sender id (default: Aaron)")
    parser.add_argument("--messages", type=int, default=12, help="How many trailing messages to inspect if no user turn is found")
    parser.add_argument("--raw", action="store_true", help="Print raw JSON lines instead of a rendered transcript")
    parser.add_argument("--stream", action="store_true", help="Also print assistant stream chunks from latest Codex rollout JSONL")
    parser.add_argument("--stream-file", default=None, help="Explicit rollout file path (defaults to latest for agent)")
    parser.add_argument("--stream-tail", type=int, default=40, help="How many trailing stream chunks to show (default: 40)")
    parser.add_argument("--find", default=None, help="Case-insensitive term to audit across session/rollout/trajectory sources")
    parser.add_argument("--find-max", type=int, default=12, help="Max matches to print per source in --find mode (default: 12)")
    parser.add_argument("--find-rollout-scope", choices=["latest", "all"], default="latest", help="Rollout search scope for --find (default: latest)")
    args = parser.parse_args()

    base = Path(args.base)
    session_file = load_session_file(base, args.agent, args.sender)
    rows = list(iter_jsonl(session_file))
    trajectory_file = session_file.with_suffix(".trajectory.jsonl")
    stream_file = Path(args.stream_file) if args.stream_file else find_latest_rollout_file(base, args.agent)
    start_index = find_last_user_index(rows)
    tail = rows[start_index:]
    if not tail:
        tail = rows[-max(1, args.messages):]

    print(f"session_file: {session_file}")
    print("---")
    for row in tail:
        if args.raw:
            print(json.dumps(row, ensure_ascii=False))
            print("---")
            continue

        text = extract_full_message(row)
        if text:
            print(text)
            print("---")

    if args.stream:
        if stream_file is None:
            print("stream_file: <not found>")
            print("note: no rollout-*.jsonl files found for this agent")
            return

        chunks = extract_rollout_stream_chunks(stream_file)
        tail_count = max(1, args.stream_tail)
        tail = chunks[-tail_count:]

        print(f"stream_file: {stream_file}")
        print(f"stream_chunks_total: {len(chunks)}")
        print(f"stream_chunks_shown: {len(tail)}")
        print("stream_note: model-side output_text chunks; Telegram draft send/edit/delete events are transport-side and may not be persisted")
        print("---")
        for index, chunk in enumerate(tail, start=1):
            ts = chunk.get("timestamp") or "unknown-ts"
            print(f"[stream:{index} @ {ts}] {chunk.get('text', '')}")
            print("---")

    if args.find:
        pattern = re.compile(re.escape(args.find), re.IGNORECASE)
        print(f"find_term: {args.find}")

        session_matches = scan_session_for_term(rows, pattern)
        print(f"find_session_matches: {len(session_matches)}")
        for match in session_matches[: max(1, args.find_max)]:
            print(
                f"[find:session row={match['row']} role={match['role']} type={match['item_type']}] "
                f"{short_snippet(match['text'])}"
            )

        rollout_files = [stream_file] if args.find_rollout_scope == "latest" else find_rollout_files(base, args.agent)
        rollout_files = [f for f in rollout_files if f is not None]

        if not rollout_files:
            print("find_rollout_matches: <rollout file not found>")
        else:
            rollout_matches = []
            files_with_hits = set()
            for rollout_file in rollout_files:
                for entry in iter_rollout_message_text(rollout_file):
                    if pattern.search(entry["text"]):
                        entry["file"] = str(rollout_file)
                        rollout_matches.append(entry)
                        files_with_hits.add(str(rollout_file))
            print(f"find_rollout_matches: {len(rollout_matches)}")
            print(f"find_rollout_files_scanned: {len(rollout_files)}")
            print(f"find_rollout_files_with_hits: {len(files_with_hits)}")
            for match in rollout_matches[: max(1, args.find_max)]:
                ts = match.get("timestamp") or "unknown-ts"
                print(
                    f"[find:rollout ts={ts} role={match['role']} type={match['item_type']} file={match['file']}] "
                    f"{short_snippet(match['text'])}"
                )

            assistant_rollout_matches = [m for m in rollout_matches if m.get("role") == "assistant"]
            print(f"find_rollout_assistant_matches: {len(assistant_rollout_matches)}")

        if trajectory_file.exists():
            trajectory_matches = scan_trajectory_for_term(trajectory_file, pattern)
            print(f"find_trajectory_matches: {len(trajectory_matches)}")
            for match in trajectory_matches[: max(1, args.find_max)]:
                print(
                    f"[find:trajectory row={match['row']} type={match['type']}] "
                    f"{short_snippet(match['text'])}"
                )
        else:
            print("find_trajectory_matches: <trajectory file not found>")


if __name__ == "__main__":
    main()
