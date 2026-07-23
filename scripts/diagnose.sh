#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
# MAX Messenger — Health Diagnostics (Webhook / Long Polling)
# ═══════════════════════════════════════════════════════════════════════
# Usage:
#   ./diagnose.sh          # basic (no E2E send)
#   ./diagnose.sh --send   # full + sends test message
# ═══════════════════════════════════════════════════════════════════════

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

PASS=0; FAIL=0; SKIP=0

check() {
    local num=$1 desc=$2 result=$3
    if [ "$result" = "pass" ]; then
        echo -e "  ${GREEN}✅${NC} $num. $desc"
        PASS=$((PASS + 1))
    elif [ "$result" = "skip" ]; then
        echo -e "  ${YELLOW}⏭️${NC} $num. $desc"
        SKIP=$((SKIP + 1))
    else
        echo -e "  ${RED}❌${NC} $num. $desc"
        FAIL=$((FAIL + 1))
    fi
}

# ── Config ────────────────────────────────────────────────────────────
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
ENV_FILE="$HERMES_HOME/.env"
CONFIG_FILE="$HERMES_HOME/config.yaml"
GATEWAY_LOG="$HERMES_HOME/logs/gateway.log"
MAX_API="https://platform-api.max.ru"

# Parse KEY=VALUE from .env (skip comments, non-var lines)
if [ -f "$ENV_FILE" ]; then
    while IFS='=' read -r key val; do
        if [ -n "$key" ] && [ -n "$val" ] && [ "${key#\#}" = "$key" ]; then
            key="${key## }"; key="${key%% *}"
            val="${val%%\#*}"; val="${val## }"
            export "$key=$val" 2>/dev/null || true
        fi
    done < "$ENV_FILE"
fi

# Determine mode
if [ -n "${MAX_WEBHOOK_URL:-}" ]; then
    MODE="webhook"
else
    MODE="long_polling"
fi

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
printf "║   MAX Messenger — Health Diagnostics (%s)     ║\n" "$MODE"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# ── 1. Plugin installed & enabled ────────────────────────────────────
desc="Plugin installed & enabled"
if command -v hermes &>/dev/null; then
    plugin_line=$(hermes plugins list 2>/dev/null | grep "max-platform")
    if echo "$plugin_line" | grep -q "enabled"; then
        version=$(echo "$plugin_line" | sed 's/.*│ *//; s/ *│.*//')
        check 1 "$desc ($version)" "pass"
    else
        check 1 "$desc" "fail"
    fi
else
    check 1 "$desc (hermes CLI not found)" "fail"
fi

# ── 2. Gateway log: MAX connected ────────────────────────────────────
desc="Gateway log: MAX connected"
if [ -f "$GATEWAY_LOG" ]; then
    last_conn=$(grep "MAX: connected" "$GATEWAY_LOG" 2>/dev/null | tail -1)
    if [ -n "$last_conn" ]; then
        echo -e "       Last: $last_conn"
        check 2 "$desc" "pass"
    else
        echo -e "       No 'MAX: connected' entries found"
        check 2 "$desc" "fail"
    fi
else
    check 2 "$desc (log file not found)" "fail"
fi

# ── 3. Mode-specific: webhook port / polling activity ────────────────
if [ "$MODE" = "webhook" ]; then
    desc="Webhook port :8646 listening"
    if ss -tlnp 2>/dev/null | grep -q "8646"; then
        pid=$(ss -tlnp 2>/dev/null | grep "8646" | sed 's/.*pid=//;s/,.*//')
        echo -e "       PID: $pid"
        check 3 "$desc" "pass"
    else
        check 3 "$desc" "fail"
    fi
else
    desc="Polling activity in logs"
    if [ -f "$GATEWAY_LOG" ]; then
        # Check for recent poll entries (last 10 lines with "MAX: poll" not being errors)
        poll_lines=$(grep "MAX: poll" "$GATEWAY_LOG" 2>/dev/null | tail -10 || true)
        poll_ok=$(echo "$poll_lines" | grep -v "poll error" | grep -v "poll HTTP" || true)
        poll_err=$(echo "$poll_lines" | grep -E "poll error|poll HTTP" || true)
        if [ -n "$poll_ok" ]; then
            echo -e "       Last: $(echo "$poll_ok" | tail -1)"
            check 3 "$desc" "pass"
        elif [ -n "$poll_lines" ]; then
            echo -e "       Only error lines: $(echo "$poll_lines" | wc -l) entries"
            check 3 "$desc (all poll entries are errors)" "fail"
        else
            echo -e "       No poll entries in log"
            check 3 "$desc (no poll activity)" "fail"
        fi
    else
        check 3 "$desc (log file not found)" "fail"
    fi
fi

# ── 4. Mode-specific: health endpoint / poll errors ──────────────────
if [ "$MODE" = "webhook" ]; then
    desc="Health endpoint"
    health=$(curl -s --max-time 5 http://localhost:8646/health 2>/dev/null | tr -d '[:space:]' || echo "")
    if [ "$health" = '{"status":"ok"}' ]; then
        check 4 "$desc" "pass"
    else
        echo -e "       Got: $health"
        check 4 "$desc" "fail"
    fi
else
    desc="No recent poll errors"
    if [ -f "$GATEWAY_LOG" ]; then
        recent_errs=$(grep -E "MAX: poll error|MAX: poll HTTP" "$GATEWAY_LOG" 2>/dev/null | tail -5 || true)
        if [ -z "$recent_errs" ]; then
            check 4 "$desc" "pass"
        else
            echo -e "       Last errors:"
            echo "$recent_errs" | sed 's/^/       /'
            check 4 "$desc" "fail"
        fi
    else
        check 4 "$desc (log file not found)" "skip"
    fi
fi

# ── 5. Subscription check ────────────────────────────────────────────
desc="Subscription status"
if [ -n "${MAX_BOT_TOKEN:-}" ]; then
    subs=$(curl -s --max-time 10 \
        -H "Authorization: $MAX_BOT_TOKEN" \
        "$MAX_API/subscriptions" 2>/dev/null || echo "")
    if [ "$MODE" = "webhook" ]; then
        webhook_path="${MAX_WEBHOOK_URL:-unknown}"
        if echo "$subs" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('subscriptions',[])))" 2>/dev/null | grep -q "^[1-9]"; then
            has_target=$(echo "$subs" | python3 -c "
import sys,json
d=json.load(sys.stdin)
target='$webhook_path'
for s in d.get('subscriptions',[]):
    if target in s.get('url',''):
        print('found')
" 2>/dev/null)
            if [ -n "$has_target" ]; then
                urls=$(echo "$subs" | python3 -c "
import sys,json
d=json.load(sys.stdin)
for s in d.get('subscriptions',[]):
    print(s.get('url',''))
" 2>/dev/null)
                echo -e "       URLs:"
                echo "$urls" | sed 's/^/         /'
                check 5 "$desc" "pass"
            else
                echo -e "       Active subscriptions found, but none match MAX_WEBHOOK_URL"
                check 5 "$desc (subscription mismatch)" "fail"
            fi
        else
            echo -e "       Response: ${subs:0:200}"
            check 5 "$desc (no active subscriptions)" "fail"
        fi
    else  # long polling
        sub_count=$(echo "$subs" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('subscriptions',[])))" 2>/dev/null || echo "0")
        if [ "$sub_count" = "0" ]; then
            check 5 "$desc (no stale subscriptions — good)" "pass"
        else
            echo -e "       WARNING: $sub_count stale subscription(s) will block polling!"
            check 5 "$desc (stale webhook subscriptions found)" "fail"
        fi
    fi
else
    check 5 "$desc (MAX_BOT_TOKEN not found in .env)" "fail"
fi

# ── 6. Token valid ────────────────────────────────────────────────────
desc="Bot token valid"
if [ -n "${MAX_BOT_TOKEN:-}" ]; then
    me=$(curl -s --max-time 10 \
        -H "Authorization: $MAX_BOT_TOKEN" \
        "$MAX_API/me" 2>/dev/null || echo "")
    username=$(echo "$me" | python3 -c "import sys,json; print(json.load(sys.stdin).get('username',''))" 2>/dev/null || echo "")
    if [ -n "$username" ]; then
        echo -e "       Bot: @$username"
        check 6 "$desc" "pass"
    elif echo "$me" | grep -q "401\|Unauthorized"; then
        check 6 "$desc (401 — invalid token)" "fail"
    else
        echo -e "       Response: ${me:0:200}"
        check 6 "$desc" "fail"
    fi
else
    check 6 "$desc (MAX_BOT_TOKEN not found)" "fail"
fi

# ── 7. E2E send (optional) ───────────────────────────────────────────
desc="E2E send test"
if [ "${1:-}" = "--send" ]; then
    target_id="${MAX_HOME_CHANNEL:-${MAX_ALLOWED_USERS%%,*}}"
    [ -z "$target_id" ] && target_id="${MAX_HOME_CHANNEL_THREAD_ID:-}"
    if [ -n "${MAX_BOT_TOKEN:-}" ] && [ -n "$target_id" ]; then
        # Extract numeric ID after colon if present (chat:286610019 → 286610019)
        target_num=$(echo "$target_id" | grep -oE '[0-9]+$' | head -1)
        param="user_id"
        # Use chat_id if target looks like a group chat
        if echo "$target_id" | grep -q "^chat:"; then
            param="chat_id"
        fi
        result=$(curl -s --max-time 10 -X POST \
            "$MAX_API/messages?${param}=${target_num:-$target_id}" \
            -H "Authorization: $MAX_BOT_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{"text":"✅ Diagnostics: bot OK","format":"markdown"}' 2>/dev/null || echo "{}")
        mid=$(echo "$result" | python3 -c "
import sys,json
try:
    d=json.load(sys.stdin)
    m=d.get('message',{})
    print(m.get('body',{}).get('mid','') or d.get('message_id',''))
except: print('')
" 2>/dev/null)
        if [ -n "$mid" ]; then
            echo -e "       mid: $mid"
            check 7 "$desc" "pass"
        else
            echo -e "       Response: ${result:0:200}"
            check 7 "$desc" "fail"
        fi
    else
        check 7 "$desc (no target user/chat configured)" "skip"
    fi
else
    check 7 "$desc (use --send to test)" "skip"
fi

# ── 8. Reasoning configured ───────────────────────────────────────────
desc="Reasoning: fresh_final_after_seconds"
if [ -f "$CONFIG_FILE" ]; then
    ffas=$(grep -A3 'max:' "$CONFIG_FILE" 2>/dev/null | grep fresh_final_after_seconds || true)
    if echo "$ffas" | grep -q "10"; then
        echo -e "       Config: $ffas"
        check 8 "$desc" "pass"
    else
        echo -e "       Not set (optional — reasoning appears in-stream without it)"
        check 8 "$desc" "skip"
    fi
else
    check 8 "$desc (config.yaml not found)" "skip"
fi

# ── Summary ────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
printf "║  ${GREEN}✅ %d passed${NC}, ${RED}❌ %d failed${NC}, ${YELLOW}⏭️ %d skipped${NC}                  ║\n" "$PASS" "$FAIL" "$SKIP"
printf "║  Mode: %s                                              ║\n" "$MODE"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

if [ "$FAIL" -gt 0 ]; then
    echo -e "${RED}Some checks failed. Check ~/.hermes/logs/gateway.log for details.${NC}"
    echo "For troubleshooting: https://gitea.rmg7.com/agent/hermes-max-integration"
    exit 1
else
    echo -e "${GREEN}All checks passed!${NC}"
    exit 0
fi
