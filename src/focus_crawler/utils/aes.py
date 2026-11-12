"""纯 Python AES-128-CBC 解密（HLS AES-128 分片用，不涉及 DRM）。"""

from __future__ import annotations


def _gf_mul(a: int, b: int) -> int:
    product = 0
    for _ in range(8):
        if b & 1:
            product ^= a
        high = a & 0x80
        a = (a << 1) & 0xFF
        if high:
            a ^= 0x1B
        b >>= 1
    return product


def _gf_inverse(value: int) -> int:
    if value == 0:
        return 0
    for candidate in range(1, 256):
        if _gf_mul(value, candidate) == 1:
            return candidate
    raise ValueError("GF(2^8) 逆元不存在")


def _rotl8(value: int, shift: int) -> int:
    return ((value << shift) | (value >> (8 - shift))) & 0xFF


def _build_sbox() -> list[int]:
    sbox: list[int] = []
    for value in range(256):
        inv = _gf_inverse(value)
        sbox.append(
            inv ^ _rotl8(inv, 1) ^ _rotl8(inv, 2) ^ _rotl8(inv, 3) ^ _rotl8(inv, 4) ^ 0x63
        )
    return sbox


SBOX = _build_sbox()
INV_SBOX = [0] * 256
for _index, _value in enumerate(SBOX):
    INV_SBOX[_value] = _index


def _expand_key(key: bytes) -> list[list[int]]:
    if len(key) != 16:
        raise ValueError("仅支持 AES-128（16 字节密钥）")
    words: list[list[int]] = [list(key[i : i + 4]) for i in range(0, 16, 4)]
    rcon = 1
    for index in range(4, 44):
        temp = list(words[index - 1])
        if index % 4 == 0:
            temp = temp[1:] + temp[:1]
            temp = [SBOX[value] for value in temp]
            temp[0] ^= rcon
            rcon = _gf_mul(rcon, 2)
        words.append([words[index - 4][i] ^ temp[i] for i in range(4)])
    return [sum(words[round * 4 : round * 4 + 4], []) for round in range(11)]


def _add_round_key(state: list[int], round_key: list[int]) -> None:
    for index in range(16):
        state[index] ^= round_key[index]


def _sub_bytes(state: list[int], box: list[int]) -> None:
    for index in range(16):
        state[index] = box[state[index]]


def _shift_rows(state: list[int]) -> None:
    for row in range(1, 4):
        values = [state[row + column * 4] for column in range(4)]
        values = values[row:] + values[:row]
        for column in range(4):
            state[row + column * 4] = values[column]


def _inv_shift_rows(state: list[int]) -> None:
    for row in range(1, 4):
        values = [state[row + column * 4] for column in range(4)]
        values = values[-row:] + values[:-row]
        for column in range(4):
            state[row + column * 4] = values[column]


def _mix_columns(state: list[int]) -> None:
    for column in range(4):
        offset = column * 4
        a0, a1, a2, a3 = state[offset : offset + 4]
        state[offset] = _gf_mul(a0, 2) ^ _gf_mul(a1, 3) ^ a2 ^ a3
        state[offset + 1] = a0 ^ _gf_mul(a1, 2) ^ _gf_mul(a2, 3) ^ a3
        state[offset + 2] = a0 ^ a1 ^ _gf_mul(a2, 2) ^ _gf_mul(a3, 3)
        state[offset + 3] = _gf_mul(a0, 3) ^ a1 ^ a2 ^ _gf_mul(a3, 2)


def _inv_mix_columns(state: list[int]) -> None:
    for column in range(4):
        offset = column * 4
        a0, a1, a2, a3 = state[offset : offset + 4]
        state[offset] = _gf_mul(a0, 14) ^ _gf_mul(a1, 11) ^ _gf_mul(a2, 13) ^ _gf_mul(a3, 9)
        state[offset + 1] = _gf_mul(a0, 9) ^ _gf_mul(a1, 14) ^ _gf_mul(a2, 11) ^ _gf_mul(a3, 13)
        state[offset + 2] = _gf_mul(a0, 13) ^ _gf_mul(a1, 9) ^ _gf_mul(a2, 14) ^ _gf_mul(a3, 11)
        state[offset + 3] = _gf_mul(a0, 11) ^ _gf_mul(a1, 13) ^ _gf_mul(a2, 9) ^ _gf_mul(a3, 14)


def encrypt_block(block: bytes, key: bytes) -> bytes:
    round_keys = _expand_key(key)
    state = list(block)
    _add_round_key(state, round_keys[0])
    for round_index in range(1, 10):
        _sub_bytes(state, SBOX)
        _shift_rows(state)
        _mix_columns(state)
        _add_round_key(state, round_keys[round_index])
    _sub_bytes(state, SBOX)
    _shift_rows(state)
    _add_round_key(state, round_keys[10])
    return bytes(state)


def decrypt_block(block: bytes, key: bytes) -> bytes:
    round_keys = _expand_key(key)
    state = list(block)
    _add_round_key(state, round_keys[10])
    for round_index in range(9, 0, -1):
        _inv_shift_rows(state)
        _sub_bytes(state, INV_SBOX)
        _add_round_key(state, round_keys[round_index])
        _inv_mix_columns(state)
    _inv_shift_rows(state)
    _sub_bytes(state, INV_SBOX)
    _add_round_key(state, round_keys[0])
    return bytes(state)


def cbc_decrypt(data: bytes, key: bytes, iv: bytes) -> bytes:
    """AES-128-CBC 解密；长度非 16 倍数时截断尾部。"""
    if len(iv) != 16:
        raise ValueError("IV 必须为 16 字节")
    usable = len(data) // 16 * 16
    previous = iv
    output = bytearray()
    for offset in range(0, usable, 16):
        block = data[offset : offset + 16]
        plain = decrypt_block(block, key)
        output.extend(bytes(a ^ b for a, b in zip(plain, previous)))
        previous = block
    return bytes(output)


def cbc_encrypt(data: bytes, key: bytes, iv: bytes) -> bytes:
    if len(iv) != 16:
        raise ValueError("IV 必须为 16 字节")
    usable = len(data) // 16 * 16
    previous = iv
    output = bytearray()
    for offset in range(0, usable, 16):
        block = bytes(a ^ b for a, b in zip(data[offset : offset + 16], previous))
        cipher = encrypt_block(block, key)
        output.extend(cipher)
        previous = cipher
    return bytes(output)


def strip_pkcs7(data: bytes) -> bytes:
    """尝试去掉 PKCS#7 填充；无法识别时原样返回。"""
    if not data:
        return data
    padding = data[-1]
    if 0 < padding <= 16 and data.endswith(bytes([padding]) * padding):
        return data[:-padding]
    return data
