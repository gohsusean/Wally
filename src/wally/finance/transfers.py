"""Transfer projection from existing certified bill and recipient catalog records."""

import re
from dataclasses import asdict, replace
from uuid import UUID

from wally.finance.models import FinanceError, Kind, digest
from wally.finance.payments import CertifiedPaymentSource


class CertifiedTransferSource(CertifiedPaymentSource):
    """A bill customer number is never a bank receiving account.

    Trusted composition maps bill-account page IDs to existing certified recipient
    Account IDs. The recipient namespace is ``bank_transfer:<bank code>``; its
    identifier is the receiving account. No certification or beneficiary is created.
    Both records must refer to the same issuer and responsibility/property scope.
    """

    required_transaction_namespace = "https://s.hongleongconnect.my"

    def __init__(self, catalog, recipients, *, invoice_accounts=None):
        super().__init__(catalog, invoice_accounts=invoice_accounts)
        self.recipients = {str(UUID(page)): record for page, record in recipients.items()}
        if len(self.recipients) != len(recipients):
            raise FinanceError("Ambiguous transfer recipient bindings.")

    def _check_route(self, definition):
        # Transfers use the reviewed HLB route, enforced by SupervisedPayments._gate
        # and the exact route fingerprint. They never log into the biller's portal.
        # Identity certificates and exact identity-chain enablement remain mandatory.
        pass

    def read(self, instance_id):
        bill = super().read(instance_id)
        catalog = self.catalog
        instance = catalog.store.get(instance_id)
        definition = catalog.store.get(instance.candidate.facts.definition)
        account = catalog.store.get(definition.candidate.facts.account)
        recipient_id = self.recipients.get(bill.account_page_id)
        if not recipient_id:
            raise FinanceError("No existing certified transfer recipient is bound to this bill.")
        recipient = catalog.store.get(recipient_id)
        if recipient.candidate.kind != Kind.ACCOUNT:
            raise FinanceError("A certified bank recipient account is required.")
        facts, owner = recipient.candidate.facts, account.candidate.facts
        if (
            facts.provider != owner.provider
            or facts.subject != owner.subject
            or facts.subject_kind != owner.subject_kind
            or facts.responsibility != "owner"
            or not re.fullmatch(r"bank_transfer:[A-Z0-9]{2,20}", facts.namespace)
            or not re.fullmatch(r"[0-9]{5,34}", facts.identifier)
        ):
            raise FinanceError("Recipient bank, issuer or responsibility scope does not match.")
        binding = catalog.binding(recipient_id)  # Existing certificate; never a new attestation.
        return replace(
            bill,
            destination=f"{facts.namespace.split(':', 1)[1]}:{facts.identifier}",
            catalog_binding=digest([bill.catalog_binding, binding, asdict(facts)]),
        )
