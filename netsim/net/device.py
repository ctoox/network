"""Packet and device model."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Callable, List, Optional

from .iface import Iface


BROADCAST_MAC = b"\xff" * 6
BROADCAST_IP = 0xFFFFFFFF


class PacketType(IntEnum):
    ICMP_ECHO_REQUEST = 0
    ICMP_ECHO_REPLY = 1
    ARP_REQUEST = 2
    ARP_REPLY = 3
    TCP = 4
    UDP = 5
    TRACEROUTE_PROBE = 6
    OSPF_HELLO = 7
    DHCP_DISCOVER = 8
    DHCP_OFFER = 9
    DHCP_REQUEST = 10
    DHCP_ACK = 11


@dataclass
class Packet:
    type: PacketType
    src_ip: "IP"
    dst_ip: "IP"
    src_mac: bytes = b"\x00\x00\x00\x00\x00\x00"
    dst_mac: bytes = b"\x00\x00\x00\x00\x00\x00"
    ttl: int = 64
    seq: int = 0
    payload: object = ""
    hops: int = 0
    through: List[str] = field(default_factory=list)
    vlan: int = 0  # 802.1Q tag; 0 = untagged

    def clone(self) -> "Packet":
        return Packet(
            type=self.type,
            src_ip=self.src_ip,
            dst_ip=self.dst_ip,
            src_mac=self.src_mac,
            dst_mac=self.dst_mac,
            ttl=self.ttl,
            seq=self.seq,
            payload=self.payload,
            hops=self.hops,
            through=list(self.through),
            vlan=self.vlan,
        )


class DeviceType(IntEnum):
    ROUTER = 0
    SWITCH = 1
    HOST = 2
    FIREWALL = 3

    @property
    def label(self) -> str:
        return {
            DeviceType.ROUTER: "Router",
            DeviceType.SWITCH: "Switch",
            DeviceType.HOST: "Host",
            DeviceType.FIREWALL: "Firewall",
        }[self]


class Vendor(IntEnum):
    HUAWEI = 0
    CISCO = 1
    H3C = 2
    RUIJIE = 3
    HUAWEI_FW = 4
    SANGFOR = 5

    @property
    def label(self) -> str:
        return {
            Vendor.HUAWEI: "Huawei",
            Vendor.CISCO: "Cisco",
            Vendor.H3C: "H3C",
            Vendor.RUIJIE: "Ruijie",
            Vendor.HUAWEI_FW: "HuaweiUSG",
            Vendor.SANGFOR: "Sangfor",
        }[self]

    @property
    def is_domestic(self) -> bool:
        """信创 (domestic/self-controlled) vendors."""
        return self in (Vendor.HUAWEI, Vendor.H3C, Vendor.RUIJIE,
                        Vendor.HUAWEI_FW, Vendor.SANGFOR)


class Device:
    def __init__(
        self,
        device_id: str,
        name: str,
        dtype: DeviceType,
        vendor: Vendor,
        net: "DeviceNetwork",
    ):
        self.id = device_id
        self.name = name
        self.type = dtype
        self.vendor = vendor
        self.net = net
        self.on_packet: Optional[Callable[[Device, Iface, Packet], None]] = None
        # optional protocol/service attachments (configured via CLI)
        self.ospf: Optional[object] = None
        self.dhcp: Optional[object] = None      # DHCP server pool (single)
        self.dhcp_pools: list = []              # DHCP server pools (per-VLAN)
        self.dhcp_client: Optional[dict] = None # DHCP client state (hosts)
        self.nat: Optional[object] = None
        self.acl: Optional[object] = None
        self.vrrp: Optional[object] = None
        self.vlans: set = set()
        self.acl_rules: dict = {}  # acl id -> list of ACLRule

    def add_iface(self, iface: Iface) -> Iface:
        self.net.add_interface(iface)
        return iface

    def get_iface(self, name: str) -> Optional[Iface]:
        return self.net.find_iface_by_name(name)

    @property
    def short_type(self) -> str:
        return {DeviceType.ROUTER: "R", DeviceType.SWITCH: "S", DeviceType.HOST: "H", DeviceType.FIREWALL: "F"}[self.type]