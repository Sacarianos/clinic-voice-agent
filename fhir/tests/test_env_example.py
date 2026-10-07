from pathlib import Path

ENV_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"

REQUIRED_KEYS = {
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
    "DEEPGRAM_API_KEY",
    "TWILIO_ACCOUNT_SID",
    "TWILIO_AUTH_TOKEN",
    "TWILIO_PHONE_NUMBER",
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "LANGFUSE_BASE_URL",
    "NGROK_AUTHTOKEN",
}


def test_example_env_file_lists_every_secret_with_an_empty_value():
    lines = [line for line in ENV_EXAMPLE.read_text().splitlines() if line and not line.startswith("#")]
    entries = dict(line.split("=", 1) for line in lines)

    assert REQUIRED_KEYS <= entries.keys()
    for key in REQUIRED_KEYS - {"LANGFUSE_BASE_URL"}:
        assert entries[key] == "", f"{key} must not carry a value"
