"""A tiny STARTTLS + AUTH SMTP server that relays to Mailpit (`make set-email ARGS=--local`).

The API's Gmail sender (EMAIL_PROVIDER=smtp) always does STARTTLS and logs in, which plain Mailpit
doesn't offer. This relay plays Gmail's part locally: it presents a throwaway self-signed
certificate for `localhost` (the API trusts it through SSL_CERT_FILE), accepts only the configured
user and app password, and forwards each message to Mailpit unchanged. ADR-0022.

The certificate is made with `cryptography`, which the API environment already has.
"""

from __future__ import annotations

import base64
import datetime as dt
import hmac
import ipaddress
import smtplib
import socket
import socketserver
import ssl
import threading
from pathlib import Path
from typing import Self


def make_localhost_cert(directory: Path) -> tuple[Path, Path]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = directory / "relay-cert.pem", directory / "relay-key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


class _Handler(socketserver.StreamRequestHandler):
    server: _Server

    def send(self, line: str) -> None:
        self.wfile.write(line.encode() + b"\r\n")
        self.wfile.flush()

    def readline(self) -> str | None:
        raw = self.rfile.readline(65536)
        return raw.decode("utf-8", "replace").rstrip("\r\n") if raw else None

    def handle(self) -> None:
        tls = authed = False
        sender: str | None = None
        recipients: list[str] = []
        self.send("220 localhost KitchenTab test relay (STARTTLS -> Mailpit)")
        while (line := self.readline()) is not None:
            verb, _, rest = line.partition(" ")
            verb = verb.upper()
            if verb in ("EHLO", "HELO"):
                extra = ["AUTH PLAIN LOGIN"] if tls else ["STARTTLS"]
                for item in ["localhost", *extra]:
                    self.send(f"250-{item}")
                self.send("250 8BITMIME")
            elif verb == "STARTTLS" and not tls:
                self.send("220 Ready to start TLS")
                conn = self.server.tls.wrap_socket(self.connection, server_side=True)
                self.connection = self.request = conn
                self.rfile = conn.makefile("rb")
                self.wfile = conn.makefile("wb")
                tls, authed = True, False
                sender, recipients = None, []
            elif verb == "AUTH":
                if not tls:
                    self.send("530 5.7.0 Must issue a STARTTLS command first")
                    continue
                authed = self._auth(rest)
                self.send(
                    "235 2.7.0 Accepted"
                    if authed
                    else "535 5.7.8 Username and Password not accepted"
                )
                self.server.auth_results.append(authed)
            elif verb == "MAIL":
                if not authed:
                    self.send("530 5.7.0 Authentication required")
                    continue
                sender, recipients = _address(rest), []
                self.send("250 OK")
            elif verb == "RCPT":
                recipients.append(_address(rest))
                self.send("250 OK")
            elif verb == "DATA":
                if not (authed and sender is not None and recipients):
                    self.send("503 5.5.1 Bad sequence of commands")
                    continue
                self.send("354 End data with <CR><LF>.<CR><LF>")
                body = self._read_data()
                try:
                    with smtplib.SMTP(
                        self.server.upstream[0], self.server.upstream[1], timeout=10
                    ) as up:
                        up.sendmail(sender, recipients, body)
                    self.server.relayed += 1
                    self.send("250 OK relayed")
                except (OSError, smtplib.SMTPException):
                    self.send("451 4.3.0 Mailpit is not reachable")
                sender, recipients = None, []
            elif verb == "RSET":
                sender, recipients = None, []
                self.send("250 OK")
            elif verb == "NOOP":
                self.send("250 OK")
            elif verb == "QUIT":
                self.send("221 Bye")
                return
            else:
                self.send("502 5.5.2 Command not implemented")

    def _auth(self, rest: str) -> bool:
        mech, _, initial = rest.partition(" ")
        try:
            if mech.upper() == "PLAIN":
                if not initial:
                    self.send("334 ")
                    initial = self.readline() or ""
                _, user, password = base64.b64decode(initial).decode().split("\0", 2)
            elif mech.upper() == "LOGIN":
                self.send("334 VXNlcm5hbWU6")
                user = base64.b64decode(self.readline() or "").decode()
                self.send("334 UGFzc3dvcmQ6")
                password = base64.b64decode(self.readline() or "").decode()
            else:
                return False
        except (ValueError, UnicodeDecodeError):
            return False
        want_user, want_password = self.server.credentials
        return hmac.compare_digest(
            user.encode(), want_user.encode()
        ) and hmac.compare_digest(password.encode(), want_password.encode())

    def _read_data(self) -> bytes:
        lines = []
        while True:
            raw = self.rfile.readline(1 << 20)
            if not raw or raw in (b".\r\n", b".\n"):
                break
            lines.append(raw[1:] if raw.startswith(b"..") else raw)
        return b"".join(lines)


def _address(rest: str) -> str:
    _, _, value = rest.partition(":")
    return value.strip().split(" ")[0].strip("<>")


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        tls: ssl.SSLContext,
        upstream: tuple[str, int],
        credentials: tuple[str, str],
    ):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.tls = tls
        self.upstream = upstream
        self.credentials = credentials
        self.relayed = 0
        self.auth_results: list[bool] = []


class StartTlsRelay:
    def __init__(
        self, directory: Path, upstream: tuple[str, int], user: str, password: str
    ):
        cert, key = make_localhost_cert(directory)
        self.cert_path = cert
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(cert, key)
        self._server = _Server(tls, upstream, (user, password))
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    @property
    def relayed(self) -> int:
        return self._server.relayed

    @property
    def auth_results(self) -> list[bool]:
        return list(self._server.auth_results)

    def __enter__(self) -> Self:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()


def upstream_reachable(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=3):
            return True
    except OSError:
        return False
