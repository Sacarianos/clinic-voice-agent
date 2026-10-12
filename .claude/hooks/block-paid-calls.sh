#!/bin/bash
# PreToolUse hook for Bash and PowerShell. Blocks a command that would call a paid API unless the command
# itself sets ALLOW_PAID_CALLS=1: pytest --llm <config>, clinic-evals without --report, or --env-file ../.env.
# Reads the hook input on stdin. Needs only bash, tr, sed and grep, so it runs from Git Bash on Windows.
# Exit 2 blocks the call and shows stderr to Claude. Any other exit lets it through.

input=$(cat | tr '\r\n\t' '   ')

# The command is the string after "command": in the input, up to its closing quote. Fall back to the whole input.
command=$(printf '%s' "$input" | sed -n 's/.*"command"[[:space:]]*:[[:space:]]*"\(\([^"\\]\|\\.\)*\)".*/\1/p')
[ -n "$command" ] || command=$input
# Undo JSON escapes that would hide a flag from the patterns below.
command=$(printf '%s' "$command" | sed 's/\\[nrt]/ /g; s/\\"/ /g')

if printf '%s' "$command" | grep -Eq "(^|[^A-Za-z0-9_])ALLOW_PAID_CALLS[[:space:]]*=[[:space:]]*['\"]?1([^0-9A-Za-z_]|$)"; then
  exit 0
fi

reason=
# --llm followed by anything but "scripted" (the free fake) picks a real model.
if printf '%s' "$command" | grep -Eq '(^|[[:space:]])--llm([[:space:]=]+|$)' &&
  ! printf '%s' "$command" | grep -Eq '(^|[[:space:]])--llm([[:space:]]+|=)scripted([^A-Za-z0-9_-]|$)'; then
  reason="pytest --llm runs the tests against a real model"
elif printf '%s' "$command" | grep -Eq '(^|[^A-Za-z0-9_-])clinic-evals([^A-Za-z0-9_-]|$)' &&
  ! printf '%s' "$command" | grep -Eq '(^|[[:space:]])--report([^A-Za-z0-9_-]|$)'; then
  reason="clinic-evals without --report runs an eval batch against real models"
elif printf '%s' "$command" | grep -Eq '(^|[[:space:]])--env-file([[:space:]=]+)[^[:space:]]*\.env([^A-Za-z0-9_.-]|$)'; then
  reason="--env-file ../.env hands real API keys to the command"
fi

if [ -n "$reason" ]; then
  {
    echo "Blocked: $reason, which spends real money (Anthropic, OpenRouter, Deepgram)."
    echo "Ask the user first, with a call count and a cost estimate. Only after they approve, run the command again"
    echo "with ALLOW_PAID_CALLS=1 in it, for example: ALLOW_PAID_CALLS=1 <the same command>"
  } >&2
  exit 2
fi
exit 0
