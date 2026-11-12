#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""纯 Python 的 NaCl sealed box（只做加密方向）。

为什么需要它：GitHub 的 Secrets API 要求把值用 libsodium 的 `crypto_box_seal`
加密后上传，而本项目坚持零第三方依赖——标准库里没有这套东西。所以这里按
NaCl 的规范手写一遍，只实现"加密"这一半（我们永远不需要解开 Secrets）。

组成：
- X25519（RFC 7748）椭圆曲线标量乘，用 Python 大整数实现蒙哥马利阶梯
- HSalsa20 / XSalsa20 流密码
- Poly1305 一次性认证码
- 组合成 crypto_box（X25519 + XSalsa20-Poly1305），再套上 sealed box 的封装格式

格式（与 libsodium 一致）：
    sealed = ephemeral_pk(32) || poly1305_tag(16) || ciphertext

注意：sealed box **不携带随机 nonce**。它的 nonce 由
`BLAKE2b-192(ephemeral_pk || recipient_pk)` 推导出来，所以封装开销固定 48 字节。
（我第一版按"随机 nonce 24 字节"写，格式对不上，libsodium 解不开。）

安全性提醒：这是纯 Python 实现，运行时会留下时序侧信道，也不做内存清零。
它只用来加密短小的 API Key / 授权码，且在本机一次性执行，可以接受；
不要用它保护高价值长期密钥。

正确性：`tests/test_nacl.py` 用 PyNaCl（参考实现）交叉验证随机样本。
"""

from __future__ import annotations

import hashlib
import os
import struct

_P = 2 ** 255 - 19
_BASE_POINT = b"\x09" + b"\x00" * 31

# --------------------------------------------------------------------------- #
# X25519
# --------------------------------------------------------------------------- #
def _clamp(scalar: bytes) -> int:
    k = bytearray(scalar)
    k[0] &= 248
    k[31] &= 127
    k[31] |= 64
    return int.from_bytes(k, "little")


def x25519(scalar: bytes, point: bytes) -> bytes:
    """RFC 7748 的 X25519：scalar * point，返回 32 字节 u 坐标。"""
    if len(scalar) != 32 or len(point) != 32:
        raise ValueError("X25519 的密钥和点都必须是 32 字节")
    k = _clamp(scalar)
    x1 = int.from_bytes(point, "little") & ((1 << 255) - 1)
    x2, z2, x3, z3 = 1, 0, x1, 1
    swap = 0
    for bit in range(254, -1, -1):
        kt = (k >> bit) & 1
        swap ^= kt
        if swap:
            x2, x3 = x3, x2
            z2, z3 = z3, z2
        swap = kt

        a = (x2 + z2) % _P
        aa = a * a % _P
        b = (x2 - z2) % _P
        bb = b * b % _P
        e = (aa - bb) % _P
        c = (x3 + z3) % _P
        d = (x3 - z3) % _P
        da = d * a % _P
        cb = c * b % _P
        x3 = (da + cb) % _P
        x3 = x3 * x3 % _P
        z3 = (da - cb) % _P
        z3 = x1 * z3 % _P * z3 % _P
        x2 = aa * bb % _P
        z2 = e * (aa + 121665 * e) % _P

    if swap:
        x2, x3 = x3, x2
        z2, z3 = z3, z2
    return (x2 * pow(z2, _P - 2, _P) % _P).to_bytes(32, "little")


def public_key(secret: bytes) -> bytes:
    return x25519(secret, _BASE_POINT)


# --------------------------------------------------------------------------- #
# Salsa20 家族
# --------------------------------------------------------------------------- #
_MASK32 = 0xFFFFFFFF
_SIGMA = (0x61707865, 0x3320646E, 0x79622D32, 0x6B206574)   # "expand 32-byte k"


def _rotl(v: int, n: int) -> int:
    v &= _MASK32
    return ((v << n) | (v >> (32 - n))) & _MASK32


def _rounds(x: list) -> list:
    """Salsa20 的 20 轮置换（列轮 + 行轮，共 10 组）。"""
    x = list(x)
    for _ in range(10):
        # 列轮
        for a, b, c, d in ((0, 4, 8, 12), (5, 9, 13, 1), (10, 14, 2, 6), (15, 3, 7, 11)):
            x[b] ^= _rotl((x[a] + x[d]) & _MASK32, 7)
            x[c] ^= _rotl((x[b] + x[a]) & _MASK32, 9)
            x[d] ^= _rotl((x[c] + x[b]) & _MASK32, 13)
            x[a] ^= _rotl((x[d] + x[c]) & _MASK32, 18)
        # 行轮
        for a, b, c, d in ((0, 1, 2, 3), (5, 6, 7, 4), (10, 11, 8, 9), (15, 12, 13, 14)):
            x[b] ^= _rotl((x[a] + x[d]) & _MASK32, 7)
            x[c] ^= _rotl((x[b] + x[a]) & _MASK32, 9)
            x[d] ^= _rotl((x[c] + x[b]) & _MASK32, 13)
            x[a] ^= _rotl((x[d] + x[c]) & _MASK32, 18)
    return x


def _salsa20_state(key: bytes, nonce: bytes) -> list:
    k = struct.unpack("<8I", key)
    n = struct.unpack("<2I", nonce)
    return [_SIGMA[0], k[0], k[1], k[2], k[3],
            _SIGMA[1], n[0], n[1], 0, 0,
            _SIGMA[2], k[4], k[5], k[6], k[7],
            _SIGMA[3]]


def hsalsa20(key: bytes, nonce16: bytes) -> bytes:
    """HSalsa20：给出 32 字节子密钥（XSalsa20 用它把 24 字节 nonce 接上）。"""
    k = struct.unpack("<8I", key)
    n = struct.unpack("<4I", nonce16)
    state = [_SIGMA[0], k[0], k[1], k[2], k[3],
             _SIGMA[1], n[0], n[1], n[2], n[3],
             _SIGMA[2], k[4], k[5], k[6], k[7],
             _SIGMA[3]]
    out = _rounds(state)
    # 不加上初始状态，只取固定的 8 个位置
    return struct.pack("<8I", out[0], out[5], out[10], out[15],
                       out[6], out[7], out[8], out[9])


def _salsa20_block(state: list, counter: int) -> bytes:
    st = list(state)
    st[8] = counter & _MASK32
    st[9] = (counter >> 32) & _MASK32
    out = _rounds(st)
    return struct.pack("<16I", *[((out[i] + st[i]) & _MASK32) for i in range(16)])


def salsa20_stream(key: bytes, nonce8: bytes, length: int) -> bytes:
    """Salsa20 密钥流（8 字节 nonce，64 字节一个块）。"""
    state = _salsa20_state(key, nonce8)
    out = bytearray()
    counter = 0
    while len(out) < length:
        out += _salsa20_block(state, counter)
        counter += 1
    return bytes(out[:length])


def xsalsa20_stream(key: bytes, nonce24: bytes, length: int) -> bytes:
    subkey = hsalsa20(key, nonce24[:16])
    return salsa20_stream(subkey, nonce24[16:], length)


def _xor(a: bytes, b: bytes) -> bytes:
    return bytes(x ^ y for x, y in zip(a, b))


# --------------------------------------------------------------------------- #
# Poly1305
# --------------------------------------------------------------------------- #
def poly1305(message: bytes, key: bytes) -> bytes:
    r = int.from_bytes(key[:16], "little") & 0x0FFFFFFC0FFFFFFC0FFFFFFC0FFFFFFF
    s = int.from_bytes(key[16:], "little")
    prime = (1 << 130) - 5
    acc = 0
    for i in range(0, len(message), 16):
        chunk = message[i:i + 16]
        n = int.from_bytes(chunk + b"\x01", "little")
        acc = ((acc + n) * r) % prime
    acc = (acc + s) % (1 << 128)
    return acc.to_bytes(16, "little")


# --------------------------------------------------------------------------- #
# crypto_box / sealed box
# --------------------------------------------------------------------------- #
def _box_key(shared: bytes) -> bytes:
    """crypto_box 的会话密钥 = HSalsa20(共享密钥, 全零)。"""
    return hsalsa20(shared, b"\x00" * 16)


def secretbox(key: bytes, nonce24: bytes, message: bytes) -> bytes:
    """XSalsa20-Poly1305：返回 tag(16) || ciphertext。"""
    stream = xsalsa20_stream(key, nonce24, 32 + len(message))
    poly_key = stream[:32]
    cipher = _xor(message, stream[32:])
    return poly1305(cipher, poly_key) + cipher


def seal(recipient_public_key: bytes, message: bytes) -> bytes:
    """crypto_box_seal：只有持有私钥的一方能解开。

    返回 ephemeral_pk(32) || tag(16) || ciphertext，比明文长 48 字节。
    """
    if len(recipient_public_key) != 32:
        raise ValueError("接收方公钥必须是 32 字节")
    if not isinstance(message, (bytes, bytearray)):
        message = str(message).encode("utf-8")
    eph_secret = os.urandom(32)
    eph_public = public_key(eph_secret)
    shared = x25519(eph_secret, recipient_public_key)
    key = _box_key(shared)
    # nonce 不是随机的，而是由双方公钥推导——这是 sealed box 的定义的一部分
    nonce = hashlib.blake2b(eph_public + recipient_public_key, digest_size=24).digest()
    return eph_public + secretbox(key, nonce, bytes(message))


def seal_base64(recipient_public_key_b64: str, message: str) -> str:
    """GitHub 要的格式：base64(sealed)。"""
    import base64
    raw = base64.b64decode(recipient_public_key_b64)
    return base64.b64encode(seal(raw, message.encode("utf-8"))).decode("ascii")
