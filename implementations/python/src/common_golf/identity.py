"""Persistent Ed25519 identities. Fingerprints are raw public keys, not hashes."""
from __future__ import annotations

import datetime
import os
from pathlib import Path
import ssl

from aioquic.quic.configuration import QuicConfiguration
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.x509.oid import NameOID

from .protocol import Draft


def fingerprint(certificate):
    key = certificate.public_key()
    if not isinstance(key, ed25519.Ed25519PublicKey):
        raise ValueError("CGP identities must use Ed25519")
    return key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()


class Identity:
    def __init__(self, directory, name="Common Golf"):
        directory = Path(directory)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.cert_path = directory / "identity.crt"
        self.key_path = directory / "identity.key"
        if self.key_path.exists():
            key = serialization.load_pem_private_key(self.key_path.read_bytes(), password=None)
            if not isinstance(key, ed25519.Ed25519PrivateKey):
                raise ValueError("CGP identities must use Ed25519")
        else:
            if self.cert_path.exists():
                raise ValueError("identity certificate exists without its private key")
            key = ed25519.Ed25519PrivateKey.generate()
            pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption())
            fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(pem)
        if not self.cert_path.exists():
            subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
            cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
                    .public_key(key.public_key()).serial_number(x509.random_serial_number())
                    .not_valid_before(datetime.datetime(2020, 1, 1))
                    .not_valid_after(datetime.datetime(2120, 1, 1)).sign(key, None))
            self.cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        cert = x509.load_pem_x509_certificate(self.cert_path.read_bytes())
        self.fingerprint = fingerprint(cert)
        actual = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
        if actual != self.fingerprint:
            raise ValueError("identity key and certificate do not match")

    def configuration(self, *, is_client, draft=Draft.THREE, expected_key=None):
        draft = Draft(draft)
        if expected_key is not None:
            validate_key(expected_key)
        if draft == Draft.THREE and is_client and expected_key is None:
            raise ValueError("draft 3 requires the advertised listener key")
        config = QuicConfiguration(is_client=is_client, alpn_protocols=[Draft(draft).alpn],
                                   verify_mode=ssl.CERT_NONE, idle_timeout=30.0 if draft == Draft.THREE else 90.0,
                                   max_datagram_frame_size=1200 if draft == Draft.THREE else None)
        config.load_cert_chain(self.cert_path, self.key_path)
        config._cgp_expected_key = expected_key
        return config


def validate_key(value):
    import re
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("discovery key must be 64 lowercase hexadecimal characters")
    return value


def tier_of(peer_fingerprint, trusted=None):
    """Missing/unregistered certificates are makers. Trust never follows a name."""
    if not peer_fingerprint:
        return "maker"
    tier = (trusted or {}).get(peer_fingerprint, "maker")
    if isinstance(tier, dict):
        tier = tier.get("tier", "maker")
    return tier if tier in ("maker", "developer", "internal") else "maker"
