package net

type DeviceType int

const (
	TypeRouter DeviceType = iota
	TypeSwitch
	TypeHost
	TypeFirewall
)

func (t DeviceType) String() string {
	switch t {
	case TypeRouter:
		return "Router"
	case TypeSwitch:
		return "Switch"
	case TypeHost:
		return "Host"
	case TypeFirewall:
		return "Firewall"
	}
	return "Unknown"
}

type Vendor int

const (
	VendorHuawei Vendor = iota
	VendorCisco
)

func (v Vendor) String() string {
	switch v {
	case VendorHuawei:
		return "Huawei"
	case VendorCisco:
		return "Cisco"
	}
	return "Unknown"
}

type Device struct {
	ID       string
	Name     string
	Type     DeviceType
	Vendor   Vendor
	Net      *DeviceNetwork
	Ifaces   map[string]*Iface
	OnPacket func(dev *Device, iface *Iface, pkt *Packet)
}

func NewDevice(id, name string, dtype DeviceType, vendor Vendor) *Device {
	return &Device{
		ID:     id,
		Name:   name,
		Type:   dtype,
		Vendor: vendor,
		Net:    NewDeviceNetwork(),
		Ifaces: map[string]*Iface{},
	}
}

func (d *Device) AddIface(iface *Iface) *Iface {
	d.Ifaces[stringsLower(iface.Name)] = iface
	d.Net.AddInterface(iface)
	return iface
}

func (d *Device) GetIface(name string) *Iface {
	return d.Ifaces[stringsLower(name)]
}

func (d *Device) ShortType() string {
	switch d.Type {
	case TypeRouter:
		return "R"
	case TypeSwitch:
		return "S"
	case TypeHost:
		return "H"
	case TypeFirewall:
		return "F"
	}
	return "D"
}

func stringsLower(s string) string {
	b := []byte(s)
	for i := range b {
		if b[i] >= 'A' && b[i] <= 'Z' {
			b[i] += 32
		}
	}
	return string(b)
}