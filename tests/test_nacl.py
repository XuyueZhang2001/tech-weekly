"""nacl_lite 的测试：X25519 用 RFC 7748 官方向量，sealed box 优先用 PyNaCl 交叉验证。

为什么需要这个测试：GitHub Secrets API 要求 libsodium 的 sealed box，
而本项目零依赖，所以手写了一份。手写的密码学实现必须证明对，
不能"看起来对"——这里用两重证据：

1. RFC 7748 的标准测试向量（权威、不依赖任何库）
2. 如果环境里有 PyNaCl，就做 20 组随机往返（用它解密我们加密的结果）

没有 PyNaCl 时第 2 步自动跳过，CI 上仍然能跑第 1 步和格式检查。

用法：
    python tests/test_nacl.py
    （可选）把 PyNaCl 装到某个目录后：PYTHONPATH=该目录 python tests/test_nacl.py
"""
import base64
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import nacl_lite  # noqa: E402

RESULTS = []


def check(name, passed, detail=""):
    RESULTS.append((name, passed))
    print(("  PASS  " if passed else "  FAIL  ") + name + (("  <- " + str(detail)) if detail and not passed else ""))


def hexb(s):
    return bytes.fromhex(s)


def test_rfc7748():
    print("[1] RFC 7748 X25519 测试向量")
    vectors = [
        ("a546e36bf0527c9d3b16154b82465edd62144c0ac1fc5a18506a2244ba449ac4",
         "e6db6867583030db3594c1a424b15f7c726624ec26b3353b10a903a6d0ab1c4c",
         "c3da55379de9c6908e94ea4df28d084f32eccf03491c71f754b4075577a28552"),
        ("4b66e9d4d1b4673c5ad22691957d6af5c11b6421e0ea01d42ca4169e7918ba0d",
         "e5210f12786811d3f4b7959d0538ae2c31dbe7106fc03c3efc4cd549c715a493",
         "95cbde9476e8907d7aade45cb4b873f88b595a68799fa152e6f8f7647aac7957"),
    ]
    for i, (scalar, point, expected) in enumerate(vectors, 1):
        got = nacl_lite.x25519(hexb(scalar), hexb(point))
        check("向量 %d" % i, got == hexb(expected), got.hex()[:32])


def test_format():
    print("[2] 封装格式")
    pk = nacl_lite.public_key(os.urandom(32))
    for n in (0, 1, 5, 100):
        sealed = nacl_lite.seal(pk, b"a" * n)
        check("%d 字节消息 -> %d 字节封包" % (n, 48 + n), len(sealed) == 48 + n, len(sealed))
    a = nacl_lite.seal(pk, b"same")
    b = nacl_lite.seal(pk, b"same")
    check("同一明文两次封装结果不同", a != b)
    check("公钥长度校验", _raises(lambda: nacl_lite.seal(b"short", b"x")))


def _raises(fn):
    try:
        fn()
        return False
    except ValueError:
        return True


def test_against_pynacl():
    print("[3] 与 PyNaCl 交叉验证")
    try:
        from nacl.public import PrivateKey, SealedBox
    except Exception as exc:  # noqa: BLE001
        print("  跳过：环境里没有 PyNaCl（%s）" % type(exc).__name__)
        return

    fails = 0
    for _ in range(20):
        sk = PrivateKey.generate()
        plain = os.urandom(24).hex().encode()
        if SealedBox(sk).decrypt(nacl_lite.seal(sk.public_key.encode(), plain)) != plain:
            fails += 1
    check("20 组随机往返", fails == 0, "%d 组失败" % fails)

    sk = PrivateKey.generate()
    for label, msg in [("空消息", b""), ("中文", "密钥已写入".encode()), ("1000 字节", os.urandom(1000))]:
        got = SealedBox(sk).decrypt(nacl_lite.seal(sk.public_key.encode(), msg))
        check("往返：" + label, got == msg)

    pub_b64 = base64.b64encode(sk.public_key.encode()).decode()
    out = nacl_lite.seal_base64(pub_b64, "sk-abcdef")
    check("seal_base64 可被参考实现解开",
          SealedBox(sk).decrypt(base64.b64decode(out)) == b"sk-abcdef")


def main_test():
    test_rfc7748()
    test_format()
    test_against_pynacl()
    passed = sum(1 for _, ok in RESULTS if ok)
    total = len(RESULTS)
    print("\nnacl_lite 测试：%d/%d 通过" % (passed, total))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main_test())
