package net

import (
	"encoding/binary"
	"fmt"
	"net"
	"strings"
)

type IP [4]byte

func ParseIP(s string) (IP, error) {
	var ip IP
	p := net.ParseIP(strings.TrimSpace(s))
	if p == nil || p.To4() == nil {
		return ip, fmt.Errorf("invalid IP address: %s", s)
	}
	copy(ip[:], p.To4())
	return ip, nil
}

func (ip IP) String() string {
	return fmt.Sprintf("%d.%d.%d.%d", ip[0], ip[1], ip[2], ip[3])
}

func (ip IP) IsZero() bool {
	return ip == IP{}
}

func (ip IP) Network(mask Mask) IP {
	return IP{
		ip[0] & mask[0],
		ip[1] & mask[1],
		ip[2] & mask[2],
		ip[3] & mask[3],
	}
}

func (ip IP) Broadcast(mask Mask) IP {
	return IP{
		ip[0] | ^mask[0],
		ip[1] | ^mask[1],
		ip[2] | ^mask[2],
		ip[3] | ^mask[3],
	}
}

type Mask [4]byte

func MaskFromLen(prefix int) Mask {
	if prefix < 0 || prefix > 32 {
		prefix = 32
	}
	var m Mask
	for i := 0; i < 4; i++ {
		if prefix >= 8 {
			m[i] = 0xFF
			prefix -= 8
		} else if prefix > 0 {
			m[i] = byte(0xFF << (8 - prefix))
			prefix = 0
		}
	}
	return m
}

func ParseMask(s string) (Mask, error) {
	s = strings.TrimSpace(s)
	if n, err := strconvAtoi(s); err == nil {
		return MaskFromLen(n), nil
	}
	var m Mask
	p := net.ParseIP(s)
	if p == nil || p.To4() == nil {
		return m, fmt.Errorf("invalid netmask: %s", s)
	}
	copy(m[:], p.To4())
	return m, nil
}

func (m Mask) PrefixLen() int {
	n := 0
	for _, b := range m {
		for i := 7; i >= 0; i-- {
			if b&(1<<uint(i)) != 0 {
				n++
			} else {
				return n
			}
		}
	}
	return n
}

func (m Mask) String() string {
	return fmt.Sprintf("%d.%d.%d.%d", m[0], m[1], m[2], m[3])
}

func strconvAtoi(s string) (int, error) {
	n := 0
	if len(s) == 0 {
		return 0, fmt.Errorf("empty")
	}
	for _, c := range s {
		if c < '0' || c > '9' {
			return 0, fmt.Errorf("not a number")
		}
		n = n*10 + int(c-'0')
	}
	return n, nil
}

func ipToUint32(ip IP) uint32 {
	return binary.BigEndian.Uint32(ip[:])
}

func uint32ToIP(v uint32) IP {
	var ip IP
	binary.BigEndian.PutUint32(ip[:], v)
	return ip
}