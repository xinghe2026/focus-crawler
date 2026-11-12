"""AES-128 实现测试（NIST 向量 + CBC 往返）。"""

from focus_crawler.utils.aes import cbc_decrypt, cbc_encrypt, encrypt_block, strip_pkcs7


def test_aes128_ecb_known_vector():
    key = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
    plain = bytes.fromhex("00112233445566778899aabbccddeeff")
    cipher = bytes.fromhex("69c4e0d86a7b0430d8cdb78070b4c55a")
    assert encrypt_block(plain, key) == cipher


def test_aes128_cbc_nist_vector():
    key = bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c")
    iv = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
    plain = bytes.fromhex("6bc1bee22e409f96e93d7e117393172a")
    cipher = bytes.fromhex("7649abac8119b246cee98e9b12e9197d")
    assert cbc_encrypt(plain, key, iv) == cipher
    assert cbc_decrypt(cipher, key, iv) == plain


def test_cbc_roundtrip_and_pkcs7():
    key = bytes(range(16))
    iv = bytes(range(16, 32))
    payload = b"SEGMENT-DATA-" * 3 + b"\x09" * 9
    encrypted = cbc_encrypt(payload, key, iv)
    decrypted = cbc_decrypt(encrypted, key, iv)
    assert strip_pkcs7(decrypted) == b"SEGMENT-DATA-" * 3
