package net

type PacketType int

const (
	PktICMPEchoRequest PacketType = iota
	PktICMPEchoReply
	PktARPRequest
	PktARPReply
	PktTCP
	PktUDP
	PktTracerouteProbe
)

type Packet struct {
	Type       PacketType
	SrcIP      IP
	DstIP      IP
	SrcMac     [6]byte
	DstMac     [6]byte
	VLAN       int
	TTL        int
	Seq        int
	Payload    string
	OrigSrcIP  IP
	OrigDstIP  IP
	Through    []string
	Hops       int
}

func (p *Packet) IsMulticast() bool {
	return p.DstIP[0] >= 224 && p.DstIP[0] <= 239
}

func (p *Packet) IsBroadcast() bool {
	return p.DstIP == (IP{255, 255, 255, 255})
}

func (p *Packet) Clone() *Packet {
	c := *p
	c.Through = append([]string(nil), p.Through...)
	return &c
}