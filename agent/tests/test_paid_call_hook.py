"""The Claude Code hook that stops an agent from running paid commands by accident, driven with sample hook inputs."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
HOOK = REPO / ".claude" / "hooks" / "block-paid-calls.sh"
SETTINGS = REPO / ".claude" / "settings.json"


def find_bash() -> str:
    """Git Bash on Windows, where a bare `bash` can be the WSL launcher; plain bash elsewhere."""
    if sys.platform == "win32" and (git := shutil.which("git")):
        git_bash = Path(git).resolve().parents[1] / "bin" / "bash.exe"
        if git_bash.exists():
            return str(git_bash)
    return shutil.which("bash") or pytest.skip("bash is not installed")


def hook_input(command: str, tool: str = "Bash", **tool_input) -> str:
    return json.dumps(
        {
            "session_id": "abc123",
            "cwd": "/work/clinic",
            "permission_mode": "default",
            "hook_event_name": "PreToolUse",
            "tool_name": tool,
            "tool_input": {"command": command, "description": "Run a command", **tool_input},
            "tool_use_id": "toolu_01ABC",
        }
    )


def run_hook(stdin: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [find_bash(), str(HOOK)], input=stdin, capture_output=True, text=True, encoding="utf-8", timeout=30
    )


BLOCKED = [
    "cd agent && uv run --env-file ../.env pytest --llm haiku",
    "cd agent && uv run pytest --llm=gemini tests/test_booking.py",
    "uv run pytest tests -k verify --llm haiku-4-5 -x",
    "cd evals && uv run clinic-evals --config haiku",
    "cd evals && uv run clinic-evals --config gemini --repeats 1 --scenario plain_book",
    "cd agent && uv run --env-file ../.env clinic-voice-server",
    "uv run --env-file=../.env python probe_latency.py",
    "cd evals\nuv run clinic-evals --config haiku",
    "ALLOW_PAID_CALLS=0 uv run pytest --llm haiku",
    "ALLOW_PAID_CALLS=10 uv run pytest --llm haiku",
    "ALLOW_PAID_CALLS=true uv run pytest --llm haiku",
    "echo ALLOW_PAID_CALLS && uv run pytest --llm haiku",
]

ALLOWED = [
    "cd agent && uv run pytest",
    "cd agent && uv run pytest --llm scripted",
    "cd agent && uv run pytest --llm=scripted -q",
    "cd evals && uv run clinic-evals --report",
    "cd evals && uv run clinic-evals --report --results-dir /tmp/results",
    "cd evals && uv run pytest",
    "git log --oneline && gh issue view 49",
    "docker compose -p clinic-x down -v",
    "uv run --env-file ../something.yaml python x.py",
    "ALLOW_PAID_CALLS=1 uv run pytest --llm haiku",
    "cd agent && ALLOW_PAID_CALLS=1 uv run --env-file ../.env pytest --llm haiku",
    "cd evals && ALLOW_PAID_CALLS=1 uv run --env-file ../.env clinic-evals --config haiku --max-dollars 1",
    "export ALLOW_PAID_CALLS=1 && uv run clinic-evals --config haiku",
    "ALLOW_PAID_CALLS='1' uv run pytest --llm haiku",
    "ALLOW_PAID_CALLS=1; uv run pytest --llm haiku",
]


@pytest.mark.parametrize("command", BLOCKED)
def test_a_command_that_would_spend_money_is_blocked_and_the_reason_says_how_to_opt_in(command):
    result = run_hook(hook_input(command))

    assert result.returncode == 2, result.stderr
    assert "Blocked" in result.stderr
    assert "ALLOW_PAID_CALLS=1" in result.stderr


@pytest.mark.parametrize("command", ALLOWED)
def test_a_free_command_or_one_that_opts_in_goes_through(command):
    result = run_hook(hook_input(command))

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""


def test_powershell_commands_are_held_to_the_same_rules():
    assert run_hook(hook_input("uv run clinic-evals --config haiku", tool="PowerShell")).returncode == 2
    assert (
        run_hook(
            hook_input("$env:ALLOW_PAID_CALLS = '1'; uv run clinic-evals --config haiku", tool="PowerShell")
        ).returncode
        == 0
    )
    assert run_hook(hook_input("$env:ALLOW_PAID_CALLS=1; uv run pytest --llm haiku", tool="PowerShell")).returncode == 0
    assert run_hook(hook_input("uv run clinic-evals --report", tool="PowerShell")).returncode == 0


def test_only_the_command_counts_not_the_description_or_the_other_fields():
    free = run_hook(hook_input("git status", description="Check that clinic-evals --config haiku left nothing behind"))
    sneaky = run_hook(hook_input("uv run pytest --llm haiku", description="ALLOW_PAID_CALLS=1"))

    assert free.returncode == 0
    assert sneaky.returncode == 2


def test_input_spread_over_several_lines_is_read_the_same():
    pretty = json.dumps(json.loads(hook_input("cd evals && uv run clinic-evals --config haiku")), indent=2)

    assert run_hook(pretty).returncode == 2


def test_input_without_a_command_is_let_through():
    assert run_hook(json.dumps({"tool_name": "Read", "tool_input": {"file_path": "/x"}})).returncode == 0
    assert run_hook("").returncode == 0


def test_the_project_settings_run_the_hook_before_every_shell_command():
    [group] = json.loads(SETTINGS.read_text(encoding="utf-8"))["hooks"]["PreToolUse"]

    assert group["matcher"] == "Bash|PowerShell"
    [handler] = group["hooks"]
    assert handler["type"] == "command"
    assert handler["shell"] == "bash"
    assert handler["command"].endswith('.claude/hooks/block-paid-calls.sh"')
    assert HOOK.exists()
