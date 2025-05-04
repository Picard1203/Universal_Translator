# src/config.py

"""
Application configuration and certificate-management utilities.

Defines default ports, file paths, WebSocket parameters, and provides
functions to generate a self-signed TLS certificate with appropriate
Subject Alternative Names (including all local IPv4 addresses).
"""

import ipaddress
import logging
import os
import secrets
import socket
import pathlib
from datetime import datetime, timedelta
from typing import Set

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

# ─── Logging Configuration ───────────────────────────────────────────────
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()
logging.getLogger().setLevel(LOG_LEVEL)
logger = logging.getLogger("UT-srv.config")

# ─── Network Configuration ───────────────────────────────────────────────
HTTP_PORT: int = 8440
WS_PORT: int = 8443

# ─── Certificate Paths ──────────────────────────────────────────────────
CERT_FILE: str = "certs/localhost+2.pem"
KEY_FILE: str  = "certs/localhost+2-key.pem"

# ─── WebSocket Handshake ────────────────────────────────────────────────
GUID: str = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

# ─── RTT / Ping Defaults ────────────────────────────────────────────────
FIXED_RTT_MS: int = 300
RTT_MEASUREMENT_INTERVAL_S: int = 60

# ─── Frame & Buffer Sizes ───────────────────────────────────────────────
HANDSHAKE_BUFFER: int      = 4096
RECEIVE_BUFFER_SIZE: int   = 4096
MAX_FRAME_PAYLOAD_SIZE: int = 4096
MAX_CONNECTIONS: int       = 100

# ─── Registration Retry (Unused) ────────────────────────────────────────
REGISTRATION_RETRY_DELAY: float = 0.1
REGISTRATION_MAX_ATTEMPTS: int  = 5


def _all_local_ipv4() -> Set[str]:
    """
    Discover all IPv4 addresses assigned to the local host.

    Includes the loopback address and any other addresses returned by
    getaddrinfo() for the system’s hostname and for None.

    Returns:
        A set of IPv4 address strings (e.g., {"127.0.0.1", "192.168.1.5"}).
    """
    ips: Set[str] = {"127.0.0.1"}
    try:
        hostname = socket.gethostname()
        for _, _, _, _, sockaddr in socket.getaddrinfo(hostname, 0, socket.AF_INET):
            ips.add(sockaddr[0])
        for _, _, _, _, sockaddr in socket.getaddrinfo(None, 0, socket.AF_INET):
            ips.add(sockaddr[0])
    except socket.gaierror as e:
        logger.warning("Could not resolve local IPv4 addresses: %s", e)
    return ips


def ensure_cert() -> None:
    """
    Generate a self-signed certificate and private key if they do not exist.

    - Certificate validity: 1 day ago → 10 years ahead.
    - SAN entries: "localhost" + all discovered local IPv4 addresses.

    Raises:
        IOError: If unable to write the certificate or key files.
    """
    cert_path = pathlib.Path(CERT_FILE)
    key_path = pathlib.Path(KEY_FILE)
    cert_path.parent.mkdir(parents=True, exist_ok=True)

    if cert_path.exists() and key_path.exists():
        return

    logger.info("Generating new self-signed certificate: %s, %s", CERT_FILE, KEY_FILE)

    # Generate private key
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    # Build Subject Alternative Names (SAN) list
    san_list: list[x509.GeneralName] = [x509.DNSName("localhost")]
    for ip_str in _all_local_ipv4():
        try:
            san_list.append(x509.IPAddress(ipaddress.ip_address(ip_str)))
        except ValueError as e:
            logger.warning("Invalid IP address %s in SAN list: %s", ip_str, e)

    # Build and sign certificate
    builder = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
        .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
        .public_key(private_key.public_key())
        .serial_number(secrets.randbits(64))
        .not_valid_before(datetime.utcnow() - timedelta(days=1))
        .not_valid_after(datetime.utcnow() + timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName(san_list), critical=False)
    )
    certificate = builder.sign(private_key, hashes.SHA256())

    # Write files
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    logger.info("Certificate generation complete.")
