package net

import (
	"fmt"
	"strings"
)

type LinkState int

const (
	LinkDown LinkState = iota
	LinkUp
)

func (s LinkState) String() string {
	if s == LinkUp {
		return "up"
	}
	return "down"
}

type Iface struct {
	Name       string
	IP         IP
	Mask       Mask
	Mac        [6]byte
	State      LinkState
	Connected  *Iface
	deviceID   string
	IsLoopback bool
}

func (i *Iface) HasIP() bool {
	return !i.IP.IsZero()
}

func (i *Iface) Network() IP {
	return i.IP.Network(i.Mask)
}

func (i *Iface) String() string {
	if !i.HasIP() {
		return fmt.Sprintf("%s unnumbered", i.Name)
	}
	return fmt.Sprintf("%s %s/%d", i.Name, i.IP.String(), i.Mask.PrefixLen())
}

type DeviceNetwork struct {
	Interfaces []*Iface
	Routes     *RouteTable
	ARP        *ARPCache
}

func NewDeviceNetwork() *DeviceNetwork {
	return &DeviceNetwork{
		Interfaces: []*Iface{},
		Routes:     NewRouteTable(),
		ARP:        NewARPCache(),
	}
}

func (d *DeviceNetwork) AddInterface(iface *Iface) {
	iface.deviceID = ""
	d.Interfaces = append(d.Interfaces, iface)
}

func (d *DeviceNetwork) FindInterfaceByName(name string) *Iface {
	for _, iface := range d.Interfaces {
		if strings.EqualFold(iface.Name, name) {
			return iface
		}
	}
	return nil
}

func (d *DeviceNetwork) FindInterfaceByIP(ip IP) *Iface {
	for _, iface := range d.Interfaces {
		if iface.HasIP() && iface.IP == ip {
			return iface
		}
	}
	return nil
}

func (d *DeviceNetwork) FindInterfaceForNetwork(network IP) *Iface {
	for _, iface := range d.Interfaces {
		if iface.HasIP() && iface.Network() == network {
			return iface
		}
	}
	return nil
}

func (d *DeviceNetwork) Loopback() *Iface {
	for _, iface := range d.Interfaces {
		if iface.IsLoopback {
			return iface
		}
	}
	return nil
}

func (d *DeviceNetwork) SetInterfaceIP(iface *Iface, ip IP, mask Mask) {
	iface.IP = ip
	iface.Mask = mask
}

func (d *DeviceNetwork) InterfaceOperState(iface *Iface) LinkState {
	if iface.State == LinkDown {
		return LinkDown
	}
	if !iface.HasIP() {
		return LinkDown
	}
	return LinkUp
}