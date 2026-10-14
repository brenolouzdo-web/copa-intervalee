# -*- coding: utf-8 -*-
"""
Web Push (notificações com o site FECHADO) — sem dependências além de `cryptography`.

Implementa:
  • RFC 8291 — criptografia da mensagem (aes128gcm)
  • RFC 8292 — identificação do servidor (VAPID, JWT ES256)

A chave VAPID é gerada sozinha na primeira execução e guardada em instance/vapid_private.pem
(guarde esse arquivo junto com o banco: se ele mudar, os celulares precisam reativar os alertas).
"""
import base64
import json
import os
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def b64u_dec(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _pub_bytes(pub) -> bytes:
    return pub.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


# ---------------------------------------------------------------- chaves VAPID
def carregar_chave(caminho: str):
    """Lê (ou cria) a chave privada VAPID. Devolve (chave_privada, chave_publica_b64url)."""
    if os.path.exists(caminho):
        with open(caminho, "rb") as fh:
            priv = serialization.load_pem_private_key(fh.read(), password=None)
    else:
        priv = ec.generate_private_key(ec.SECP256R1())
        pem = priv.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                 serialization.NoEncryption())
        fd = os.open(caminho, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(pem)
    return priv, b64u(_pub_bytes(priv.public_key()))


def _jwt_vapid(priv, endpoint: str, subject: str, agora=None) -> str:
    u = urlparse(endpoint)
    cab = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    corpo = b64u(json.dumps({"aud": f"{u.scheme}://{u.netloc}", "exp": int((agora or time.time()) + 12 * 3600),
                             "sub": subject}, separators=(",", ":")).encode())
    der = priv.sign(f"{cab}.{corpo}".encode(), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    return f"{cab}.{corpo}.{b64u(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


# ---------------------------------------------------------------- criptografia (RFC 8291)
def criptografar(dados: bytes, ua_public: bytes, auth_secret: bytes, *, as_private=None, salt=None) -> bytes:
    """Devolve o corpo `aes128gcm` pronto para o POST. `as_private`/`salt` só existem para testes."""
    as_private = as_private or ec.generate_private_key(ec.SECP256R1())
    as_public = _pub_bytes(as_private.public_key())
    salt = salt or os.urandom(16)
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    ecdh = as_private.exchange(ec.ECDH(), ua_key)
    ikm = HKDF(hashes.SHA256(), 32, auth_secret, b"WebPush: info\x00" + ua_public + as_public).derive(ecdh)
    cek = HKDF(hashes.SHA256(), 16, salt, b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt, b"Content-Encoding: nonce\x00").derive(ikm)
    cifrado = AESGCM(cek).encrypt(nonce, dados + b"\x02", None)
    return salt + (4096).to_bytes(4, "big") + bytes([len(as_public)]) + as_public + cifrado


# ---------------------------------------------------------------- envio
def enviar(priv, pub_b64: str, subject: str, sub: dict, payload: dict, ttl=3600, timeout=10) -> int:
    """Envia uma notificação. Devolve o status HTTP (201 = aceita; 404/410 = inscrição morta)."""
    corpo = criptografar(json.dumps(payload, ensure_ascii=False).encode(), b64u_dec(sub["p256dh"]),
                         b64u_dec(sub["auth"]))
    req = urllib.request.Request(sub["endpoint"], data=corpo, method="POST", headers={
        "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream",
        "TTL": str(ttl), "Urgency": "high",
        "Authorization": f"vapid t={_jwt_vapid(priv, sub['endpoint'], subject)}, k={pub_b64}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


def enviar_para_todos(priv, pub_b64, subject, subs, payload, mortas_cb=None):
    """Envia em paralelo; chama mortas_cb(lista_de_endpoints) com as inscrições expiradas."""
    mortas = []

    def um(s):
        if enviar(priv, pub_b64, subject, s, payload) in (404, 410):
            mortas.append(s["endpoint"])

    if subs:
        with ThreadPoolExecutor(max_workers=min(16, len(subs))) as ex:
            list(ex.map(um, subs))
    if mortas and mortas_cb:
        mortas_cb(mortas)
    return len(subs) - len(mortas)
