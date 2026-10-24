"""Production Telegram composition for the existing supervised HLB worker.

Startup constructs adapters only: no bank login, credential read, payment, email
or Notion write. Live execution is deliberately locked off in trusted code.
Absent reviewed route data is a preparation error, not an invented bank profile.
"""

import hashlib
import marshal
import os

import httpx
import yaml

from wally.adapters.browser.hlb_transfer import HLBTransferProfile, HLBTransferProvider
from wally.adapters.browser.playwright_adapter import PlaywrightBrowserAdapter
from wally.adapters.google.adapter import create_communications_provider
from wally.adapters.google.receipts import GmailReceiptMailer
from wally.adapters.notion.native_receipts import (
    NativeNotionPaymentHistory,
    production_payment_history_target,
)
from wally.adapters.secrets import create_secrets_provider
from wally.audit.logger import AuditLogger
from wally.codex.__main__ import NoFinancialConfirmation
from wally.finance.bill_identity import RetainedInvoiceAccounts
from wally.finance.config import load_profiles, load_sources
from wally.finance.models import FinanceError
from wally.finance.notion import VERSION, NotionFinanceReader
from wally.finance.payments import SupervisedPayments
from wally.finance.receipts import ReceiptConfirmations
from wally.finance.service import FinanceService
from wally.finance.store import FinanceStore
from wally.finance.transfers import CertifiedTransferSource
from wally.knowledge.registry import KnowledgeRegistry
from wally.runtime.secret_resolver import GovernedSecretsResolver


def load_hlb_route(path):
    """Reviewed configuration supplies data, never execution authority.

    An incomplete route stays absent. Synthetic profiles and enablement flags
    cannot turn this production composition into a simulation or live executor.
    """
    try:
        data = yaml.safe_load(path.read_text())
        if (
            not isinstance(data, dict)
            or set(data) != {"schema_version", "profile", "recipients"}
            or data["schema_version"] != 1
            or not isinstance(data["recipients"], dict)
        ):
            raise ValueError
        profile = data["profile"]
        if profile is not None:
            profile = dict(profile)
            for name in (
                "review_fields", "beneficiary_fields", "history_fields",
                "read_requests", "payment_requests", "payment_fields",
            ):
                profile[name] = tuple(tuple(pair) for pair in profile[name])
            profile = HLBTransferProfile(**profile)
            if profile.sandbox is not False or profile.submission_enabled is not False:
                raise ValueError
            _ = profile.fingerprint
        return profile, data["recipients"]
    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError):
        raise FinanceError(
            "Invalid reviewed HLB payment configuration; payment is blocked."
        ) from None


def build_production_payments(settings, store, authority):
    profile, recipients = load_hlb_route(settings.project_root / "config" / "hlb-payments.yaml")
    registry = KnowledgeRegistry(settings.knowledge_registry_database)

    def notion_client():
        return httpx.Client(
            base_url="https://api.notion.com/v1",
            headers={
                "Authorization": "Bearer " + (settings.notion_api_key or ""),
                "Notion-Version": VERSION,
            },
            timeout=30, follow_redirects=False,
        )

    reader = notion_client()
    finance_path = settings.project_root / "config" / "finance.yaml"
    catalog = FinanceService(
        FinanceStore(settings.ops_database), authority=authority,
        approval=NoFinancialConfirmation(), registry=registry,
        reader=NotionFinanceReader(reader), sources=lambda: load_sources(finance_path),
        profiles=lambda: load_profiles(finance_path), dry_run=settings.dry_run,
    )
    source = CertifiedTransferSource(
        catalog, recipients, invoice_accounts=RetainedInvoiceAccounts({}),
    )
    evidence_directory = settings.project_root / "data" / "supervised-payment"
    history = NativeNotionPaymentHistory(
        production_payment_history_target(), reader, registry,
        write_client=notion_client, evidence_directory=evidence_directory,
    )
    resolver = GovernedSecretsResolver(
        create_secrets_provider(settings), audit=AuditLogger(settings.audit_directory),
    )
    provider = HLBTransferProvider(
        PlaywrightBrowserAdapter(
            headless=settings.browser_headless, channel=settings.browser_channel,
        ),
        profile, resolver,
    )
    confirmations = ReceiptConfirmations(
        source, GmailReceiptMailer(create_communications_provider(settings)), evidence_directory,
    )
    return SupervisedPayments(
        store, authority, source, provider, history, resolver,
        enabled=lambda: False, live_validated=False, preparation_enabled=lambda: True,
        confirmations=confirmations,
    )


def runtime_status(payments):
    """Evidence emitted by the running process from its imported implementations."""
    from wally.telegram.payment_approvals import TelegramPaymentApprovals

    provider = payments.provider
    return {
        "pid": os.getpid(),
        "provider": f"{type(provider).__module__}.{type(provider).__qualname__}",
        "policy": "exact_telegram_before_hlb_entry_v1",
        "live_submission_enabled": payments.enabled(),
        "profile_configured": provider.profile is not None,
        "bank_submission_enabled": bool(
            provider.profile is not None and provider.profile.submission_enabled
        ),
        "review_code_sha256": hashlib.sha256(
            marshal.dumps(type(provider).enter_review.__code__)
        ).hexdigest(),
        "request_code_sha256": hashlib.sha256(
            marshal.dumps(type(provider)._request_allowed.__code__)
        ).hexdigest(),
        "approval_code_sha256": hashlib.sha256(
            marshal.dumps(TelegramPaymentApprovals.handle.__code__)
        ).hexdigest(),
        "preparation_code_sha256": hashlib.sha256(
            marshal.dumps(TelegramPaymentApprovals.prepare.__code__)
        ).hexdigest(),
    }
