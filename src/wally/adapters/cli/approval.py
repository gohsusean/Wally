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
