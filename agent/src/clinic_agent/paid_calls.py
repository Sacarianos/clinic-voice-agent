"""The opt-in every paid run needs. Real-model test runs and eval batches spend real money, so nothing starts them by accident."""

from collections.abc import Mapping

OPT_IN = "ALLOW_PAID_CALLS"


class PaidCallsNotAllowed(Exception):
    pass


def require_opt_in(env: Mapping[str, str], *, what: str, estimate: str) -> None:
    """Raises unless the environment sets ALLOW_PAID_CALLS to exactly 1. The refusal says what the run would cost."""
    if env.get(OPT_IN) == "1":
        return
    raise PaidCallsNotAllowed(
        f"{what} calls paid APIs and would cost {estimate}. Nothing was called.\n"
        f"To go ahead, set {OPT_IN}=1 for this one command, for example: {OPT_IN}=1 {what}\n"
        f"Don't put it in .env or export it for the whole shell: the guard is there to make each paid run a choice."
    )
