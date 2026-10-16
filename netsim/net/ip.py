"""IP address and netmask utilities."""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress


@dataclass(frozen=True)
class IP:
    """4-byte IPv4 address."""

    value: int

    def __init__(self, value: int):
        object.__setattr__(self, "value", value & 0xFFFFFFFF)

    @classmethod
    def parse(cls, s: str) -> "IP":
        return cls(int(ipaddress.IPv4Address(s.strip())))

    @classmethod
    def from_bytes(cls, b) -> "IP":
        return cls(int.from_bytes(bytes(b), "big"))

    def to_bytes(self) -> bytes:
        return self.value.to_bytes(4, "big")

    def __str__(self) -> str:
        return str(ipaddress.IPv4Address(self.value))

    def __eq__(self, other) -> bool:
        return isinstance(other, IP) and self.value == other.value

    def __hash__(self) -> int:
        return hash(self.value)

    def is_zero(self) -> bool:
        return self.value == 0

    def network(self, mask: "Mask") -> "IP":
        return IP(self.value & mask.value)

    def broadcast(self, mask: "Mask") -> "IP":
        return IP(self.value | (~mask.value & 0xFFFFFFFF))

    def is_multicast(self) -> bool:
        return 0xE0000000 <= self.value <= 0xEFFFFFFF

    def is_broadcast(self) -> bool:
        return self.value == 0xFFFFFFFF


@dataclass(frozen=True)
class Mask:
    """IPv4 netmask (contiguous)."""

    value: int

    def __init__(self, value: int):
        object.__setattr__(self, "value", value & 0xFFFFFFFF)

    @classmethod
    def from_len(cls, prefix: int) -> "Mask":
        prefix = max(0, min(32, prefix))
        if prefix == 0:
            return cls(0)
        return cls(0xFFFFFFFF << (32 - prefix) & 0xFFFFFFFF)

    @classmethod
    def parse(cls, s: str) -> "Mask":
        s = s.strip()
        if s.isdigit():
            return cls.from_len(int(s))
        return cls(int(ipaddress.IPv4Address(s)))

    def prefix_len(self) -> int:
        return bin(self.value).count("1")

    def __str__(self) -> str:
        return str(ipaddress.IPv4Address(self.value))

    def __eq__(self, other) -> bool:
        return isinstance(other, Mask) and self.value == other.value

    def __hash__(self) -> int:
        return hash(self.value)