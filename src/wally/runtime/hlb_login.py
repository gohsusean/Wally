"""Operator-only HLB auth/enrollment on reviewed selectors; never a payment route.

Run --help for references/selectors. No App bootstrap, model tool or live provider
is started unless the owner explicitly invokes this command.
"""

import argparse
from pathlib import Path

from wally.adapters.browser.authentication import LoginState, LoginStep
from wally.adapters.browser.hlb import HLB_ORIGIN, REFERENCE, HLBSecurityPicture, read_reference
from wally.adapters.browser.playwright_adapter import PlaywrightBrowserAdapter
from wally.adapters.secrets import create_secrets_provider
from wally.audit.logger import AuditLogger
from wally.config.loader import load_settings
from wally.runtime.secret_resolver import GovernedSecretsResolver


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--enroll", action="store_true", help="Confirm picture locally; stop before password"
    )
    modes.add_argument(
        "--replace-picture", action="store_true", help="Explicitly replace enrolled picture"
    )
    modes.add_argument(
        "--enroll-and-login",
        action="store_true",
        help="Confirm/enroll, then log in in this session",
    )
    parser.add_argument("--username-ref", required=True)
    parser.add_argument("--password-ref", required=True)
    parser.add_argument(
        "--picture-selector",
        default="xpath=//input[@id='idSBCBConfirmPic']/preceding::img[1]",
        help="Reviewed unique security IMG selector",
    )
    parser.add_argument("--checkbox-selector", default="#idSBCBConfirmPic")
    parser.add_argument("--password-selector", default="#idPswd")
    parser.add_argument("--submit-selector", default="#idBtnSubmit2")
    parser.add_argument(
        "--authenticated-selector",
        default='a[href*="logout" i]',
        help="Reviewed authenticated-only selector",
    )
    parser.add_argument("--authenticated-frame-selector", default="iframe")
    args = parser.parse_args()
    enrolling = args.enroll or args.replace_picture or args.enroll_and_login
    browser = PlaywrightBrowserAdapter(headless=False, channel="chrome")
    values = ()
    phase = "local picture enrollment check"
    try:
        if not enrolling:
            read_reference(REFERENCE)  # Fail before credential retrieval if not enrolled.
        settings = load_settings()
        resolver = GovernedSecretsResolver(
            create_secrets_provider(settings),
            audit=AuditLogger(Path(__file__).resolve().parents[3] / "data/audit"),
        )
        refs = (
            (args.username_ref,)
            if enrolling and not args.enroll_and_login
            else (args.username_ref, args.password_ref)
        )
        phase = "1Password credential retrieval"
        print("HLB: waiting for local 1Password authorization.", flush=True)
        values = resolver.resolve_many(refs, authorized=True, purpose="owner_hlb_auth_only")
        phase = "official HLB navigation"
        session = browser.open_session(url=HLB_ORIGIN + "/", allowed_origins=(HLB_ORIGIN,))
        phase = "HLB login form readiness"
        browser.wait_for_hlb_login(session.session_id)
        phase = "HLB username submission"
        result = browser.submit_authentication_step(
            session.session_id,
            LoginStep("#idLoginId", (args.picture_selector,), submit_selector="#idBtnSubmit1"),
            values[0],
            allowed_origins=(HLB_ORIGIN,),
            before_submit=lambda: None,
        )
        if result.state != LoginState.ADVANCED:
            print("HLB username transition not verified; stopped without password entry.")
            return 1
        picture = HLBSecurityPicture(args.picture_selector, args.checkbox_selector)
        if enrolling:
            phase = "owner-confirmed picture enrollment"

            def confirm_owner():
                return (
                    input(
                        "Inspect the actual security picture in the local HLB browser. "
                        "To enroll this exact picture, type CONFIRM HLB PICTURE: "
                    )
                    == "CONFIRM HLB PICTURE"
                )

            browser.enroll_hlb_security_picture(
                session.session_id,
                picture,
                confirm_owner=confirm_owner,
                guard=lambda: None,
                replace=args.replace_picture,
            )
            print("HLB picture enrolled locally.")
            if not args.enroll_and_login:
                print("No password submitted; run auth-only login next.")
                return 0
        phase = "enrolled picture confirmation and password submission"
        result = browser.submit_hlb_password(
            session.session_id,
            picture,
            LoginStep(
                args.password_selector,
                (args.authenticated_selector,),
                submit_selector=args.submit_selector,
                next_frame_selector=args.authenticated_frame_selector,
            ),
            values[1],
            guard=lambda: None,
        )
        print("HLB auth-only result: " + result.state.value)
        return 0 if result.state == LoginState.ADVANCED else 1
    except Exception:
        # Never surface Playwright DOM/URLs, credential values, images or hashes.
        print(f"HLB login/enrollment stopped during {phase}. No automatic retry.")
        return 1
    finally:
        values = ()
        browser.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
