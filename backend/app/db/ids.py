"""Generación de UUID versión 7 (ordenados por tiempo).

Python 3.12 y 3.13 no traen uuid7 (llega en 3.14), así que lo generamos aquí
siguiendo el RFC 9562: 48 bits de milisegundos + bits aleatorios.
Al ser crecientes en el tiempo, los índices B-tree no se fragmentan
como con los UUID v4 totalmente aleatorios.
"""

import os
import time
import uuid


def uuid7() -> uuid.UUID:
    ms = time.time_ns() // 1_000_000
    rand = int.from_bytes(os.urandom(10), "big")  # 80 bits aleatorios
    value = (ms & ((1 << 48) - 1)) << 80  # 48 bits: marca de tiempo
    value |= 0x7 << 76  # 4 bits: versión 7
    value |= ((rand >> 68) & 0xFFF) << 64  # 12 bits aleatorios
    value |= 0b10 << 62  # 2 bits: variante RFC
    value |= rand & ((1 << 62) - 1)  # 62 bits aleatorios
    return uuid.UUID(int=value)
