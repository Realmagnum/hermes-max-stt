#!/usr/bin/env python3
"""
Apply optional MEDIA-only delivery support for MAX in Hermes core.

Without this patch, ``hermes send --to max:USER_ID "MEDIA:/path/file"``
(media-only, no text) fails with:

    send_message MEDIA delivery is only supported for telegram, discord, ...

This is a core limitation in ``tools/send_message_tool.py`` — the hardcoded
platform list doesn't include ``max``. The patch adds a MAX handler before
the generic "Non-media platforms" block so media-only delivery works.

Text+media sends (e.g. ``hermes send "caption MEDIA:/path"``) work without
this patch, because the core falls through to the plugin's standalone sender
which handles files natively.

Usage:
    python3 scripts/apply-core-fix.py [--revert]

Effect:
    Adds ~26 lines to ~/.hermes/hermes-agent/tools/send_message_tool.py
    that route MAX media-only sends through the plugin's standalone_sender_fn.
"""

import os
import sys

CORE_FILE = os.path.expanduser("~/.hermes/hermes-agent/tools/send_message_tool.py")

INSERTION = '''
    # --- Max: native media delivery via the registry's standalone_sender_fn.
    # The plugin (hermes-max-integration) uploads each file through the
    # 3-step MAX Bot API protocol (POST /uploads -> POST multipart -> token)
    # so images/video/audio arrive as native attachments.
    if platform.value == "max" and media_files:
        from gateway.platform_registry import platform_registry as _pr_max
        from hermes_cli.plugins import discover_plugins as _dp_max
        _dp_max()
        _max_entry = _pr_max.get("max")
        if _max_entry is None or _max_entry.standalone_sender_fn is None:
            return {"error": "Max plugin not registered or missing standalone_sender_fn"}
        last_result = None
        for i, chunk in enumerate(chunks):
            is_last = (i == len(chunks) - 1)
            result = await _max_entry.standalone_sender_fn(
                pconfig,
                chat_id,
                chunk,
                media_files=media_files if is_last else None,
                thread_id=thread_id,
            )
            if isinstance(result, dict) and result.get("error"):
                return result
            last_result = result
        return last_result
'''

MARKER_BEFORE = "    # --- Non-media platforms ---"
MARKER_AFTER = "    if media_files and not message.strip():"


def apply_patch() -> bool:
    with open(CORE_FILE) as f:
        content = f.read()

    if "platform.value == \"max\" and media_files" in content:
        print("✅ Core already patched — nothing to do.")
        return True

    pos = content.find(MARKER_BEFORE)
    if pos == -1:
        print("❌ Could not find insertion marker in core file.")
        return False

    new_content = content[:pos] + INSERTION + "    " + content[pos:]
    with open(CORE_FILE, "w") as f:
        f.write(new_content)

    print("✅ Core patched. MEDIA-only delivery now works for MAX.")
    print(f"   File: {CORE_FILE}")
    return True


def revert_patch() -> bool:
    with open(CORE_FILE) as f:
        content = f.read()

    lines = content.split("\n")
    new_lines = []
    skip_block = False
    block_start = "if platform.value == \"max\" and media_files:"
    found = False

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.lstrip()

        if not found and stripped == block_start:
            found = True
            skip_block = True
            # Skip until we find the next top-level section
            indent = len(line) - len(stripped)
            i += 1
            while i < len(lines):
                next_line = lines[i]
                next_stripped = next_line.lstrip()
                next_indent = len(next_line) - len(next_stripped)
                if next_line.strip() and next_indent <= indent and not next_line.startswith(" " * (indent + 4)):
                    skip_block = False
                    break
                i += 1
            new_lines.append(next_line)
        elif not skip_block:
            new_lines.append(line)
        i += 1

    if not found:
        print("❌ No MAX patch found to revert.")
        return False

    with open(CORE_FILE, "w") as f:
        f.write("\n".join(new_lines))

    print("✅ Core patch reverted.")
    return True


if __name__ == "__main__":
    if "--revert" in sys.argv:
        revert_patch()
    else:
        if not os.path.exists(CORE_FILE):
            print(f"❌ Core file not found: {CORE_FILE}")
            sys.exit(1)
        apply_patch()
