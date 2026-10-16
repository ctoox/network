package net

import (
	"fmt"
	"sort"
)

type Route struct {
	Prefix  IP
	Mask    Mask
	NextHop IP
	Iface   *Iface
	Proto   string
	Metric  int
	Tag     string
}

func (r *Route) String() string {
	nextHop := "Direct"
	if r.NextHop != (IP{}) {
		nextHop = r.NextHop.String()
	}
	proto := r.Proto
	if proto == "" {
		proto = "S"
	}
	return fmt.Sprintf("%s/%d  %s  %s  %d", r.Prefix.String(), r.Mask.PrefixLen(), proto, nextHop, r.Metric)
}

type RouteTable struct {
	routes []*Route
}

func NewRouteTable() *RouteTable {
	return &RouteTable{}
}

func (t *RouteTable) Add(r *Route) {
	t.Remove(r.Prefix, r.Mask, r.Proto)
	t.routes = append(t.routes, r)
}

func (t *RouteTable) Remove(prefix IP, mask Mask, proto string) {
	out := t.routes[:0]
	for _, r := range t.routes {
		if r.Prefix == prefix && r.Mask == mask && (proto == "" || r.Proto == proto) {
			continue
		}
		out = append(out, r)
	}
	t.routes = out
}

func (t *RouteTable) ClearProto(proto string) {
	out := t.routes[:0]
	for _, r := range t.routes {
		if proto == "" || r.Proto == proto {
			continue
		}
		out = append(out, r)
	}
	t.routes = out
}

// Lookup returns the best route for dst using longest-prefix-match.
func (t *RouteTable) Lookup(dst IP) *Route {
	var best *Route
	bestBits := -1
	for _, r := range t.routes {
		if r.Prefix == dst.Network(r.Mask) {
			bits := r.Mask.PrefixLen()
			if bits > bestBits {
				best = r
				bestBits = bits
			}
		}
	}
	return best
}

func (t *RouteTable) All() []*Route {
	out := make([]*Route, len(t.routes))
	copy(out, t.routes)
	return out
}

func (t *RouteTable) Len() int {
	return len(t.routes)
}

func (t *RouteTable) Sorted() []*Route {
	out := t.All()
	sort.Slice(out, func(i, j int) bool {
		bi := out[i].Mask.PrefixLen()
		bj := out[j].Mask.PrefixLen()
		if bi != bj {
			return bi > bj
		}
		ci := ipToUint32(out[i].Prefix)
		cj := ipToUint32(out[j].Prefix)
		return ci < cj
	})
	return out
}