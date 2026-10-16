package net

import (
	"fmt"
	"time"
)

type ARPEntry struct {
	IP      IP
	Mac     [6]byte
	Iface   *Iface
	Expires time.Time
}

type ARPCache struct {
	entries []*ARPEntry
}

func NewARPCache() *ARPCache {
	return &ARPCache{}
}

func (c *ARPCache) Set(ip IP, mac [6]byte, iface *Iface, ttl time.Duration, now time.Time) {
	for _, e := range c.entries {
		if e.IP == ip && e.Iface == iface {
			e.Mac = mac
			e.Expires = now.Add(ttl)
			return
		}
	}
	c.entries = append(c.entries, &ARPEntry{IP: ip, Mac: mac, Iface: iface, Expires: now.Add(ttl)})
}

func (c *ARPCache) Get(ip IP) *ARPEntry {
	for _, e := range c.entries {
		if e.IP == ip {
			return e
		}
	}
	return nil
}

func (c *ARPCache) Delete(ip IP) {
	out := c.entries[:0]
	for _, e := range c.entries {
		if e.IP != ip {
			out = append(out, e)
		}
	}
	c.entries = out
}

func (c *ARPCache) Prune(now time.Time) {
	out := c.entries[:0]
	for _, e := range c.entries {
		if now.Before(e.Expires) {
			out = append(out, e)
		}
	}
	c.entries = out
}

func (c *ARPCache) All() []*ARPEntry {
	out := make([]*ARPEntry, len(c.entries))
	copy(out, c.entries)
	return out
}

func (c *ARPCache) String() string {
	s := "  IP ADDRESS      MAC ADDRESS     EXPIRE\n"
	for _, e := range c.entries {
		s += fmt.Sprintf("  %-15s %02x%02x.%02x%02x.%02x%02x  %s\n",
			e.IP.String(), e.Mac[0], e.Mac[1], e.Mac[2], e.Mac[3], e.Mac[4], e.Mac[5], e.Expires.Format("15:04:05"))
	}
	return s
}

func (c *ARPCache) HasMac(mac [6]byte) bool {
	for _, e := range c.entries {
		if e.Mac == mac {
			return true
		}
	}
	return false
}