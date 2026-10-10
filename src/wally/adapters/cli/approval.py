"""CLI-based approval prompts."""


class CLIApprovalProvider:
    """Prompt the user on stdin for y/n approval."""

    def request_approval(self, summary: str, *, action_class: str) -> bool:
        print(f"\n⚠️  Approval required ({action_class})")
        print(f"   {summary}")
        while True:
            try:
                answer = input("   Approve? [y/n] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\n   Denied (interrupted).")
                return False
            if answer in {"y", "yes"}:
                return True
            if answer in {"n", "no"}:
                return False
            print("   Please enter y or n.")


class TerminalHumanConfirmation:
    """Optional exact-review admin provider, under the existing local-owner assumption.

    Register only from trusted local CLI/REPL composition. Agent pipe input cannot
    provide confirmation. The native biometric provider is used for local Codex.
    """

    method = "local_terminal_exact_review"

    def __init__(self, approval: CLIApprovalProvider | None = None):
        self.approval = approval or CLIApprovalProvider()

    def confirm(self, review) -> bool:
        import sys

        if review.channel not in {"cli", "repl"} or not sys.stdin.isatty():
            return False
        return (
            self.approval.request_approval(
                "Exact Wally review:\n" + review.presentation,
                action_class=review.purpose,
            )
            is True
        )
