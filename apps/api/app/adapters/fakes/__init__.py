"""The fakes used in local dev and every test (CLAUDE.md rule 7, TEST_PLAN §2).

Google sign-in and Web Push are faked by separate local services in `fakeservices/` (mock OIDC
issuer, fake push service) so the real client code paths run against them.
"""

from app.adapters.email import MailpitClient, SmtpEmailSender
from app.adapters.extractor import FakeExtractor
from app.adapters.sms import FakeSms

__all__ = ["FakeExtractor", "FakeSms", "MailpitClient", "SmtpEmailSender"]
