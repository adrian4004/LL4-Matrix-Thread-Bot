#!/usr/bin/env python3
"""PreToolUse hook: keep the agent away from `.env` (the bot's token lives there).

Stdlib only. Denies file tools on any `.env*` file except `.env.example`, and
Bash commands that mention such a file. Fails open on its own breakage.
"""

import json
import re
import sys

ENV_FILE = re.compile(r"(^|[\s/'\"=])\.env(?!\.example)(\.[\w-]+)?(?=$|[\s'\";|&)])")


def main() -> None:
    try:
        data = json.load(sys.stdin)
        tool_input = data.get("tool_input") or {}
        target = " ".join(
            str(tool_input.get(key, "")) for key in ("file_path", "path", "pattern", "command")
        )
    except Exception:
        return
    if ENV_FILE.search(target):
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "blocked: .env holds secrets — use .env.example",
            }
        }))


if __name__ == "__main__":
    main()
