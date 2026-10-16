package net

import (
	"fmt"
	"log"
	"sync"
	"time"
)

const (
	ARPTTL        = 20 * time.Minute
	DefaultTTL    = 64
	defaultDelay  = 10 * time.Millisecond
	defaultJitter = 1
)

type World struct {
	mu      sync.Mutex
	devices map[string]*Device
	links   []*Link
	seq     int
	now     time.Time
	onEvent EventCallback
	pending map[string]chan bool // key: "dst|seq"
}

type Link struct {
	ID    string
	A     *Iface
	B     *Iface
	Delay time.Duration
}

type EventCallback func(dev *Device, iface *Iface, pkt *Packet, action string, detail string)

func NewWorld(onEvent EventCallback) *World {
	return &World{
		devices: map[string]*Device{},
		now:     time.Date(2026, 1, 1, 0, 0, 0, 0, time.Local),
		onEvent: onEvent,
		pending: map[string]chan bool{},
	}
}

func (w *World) Now() time.Time {
	w.mu.Lock()
	defer w.mu.Unlock()
	return w.now
}

func (w *World) Advance(step time.Duration) {
	w.mu.Lock()
	w.now = w.now.Add(step)
	w.mu.Unlock()
}

func (w *World) Devices() []*Device {
	w.mu.Lock()
	defer w.mu.Unlock()
	out := make([]*Device, 0, len(w.devices))
	for _, d := range w.devices {
		out = append(out, d)
	}
	return out
}

func (w *World) GetDevice(id string) *Device {
	w.mu.Lock()
	defer w.mu.Unlock()
	return w.devices[id]
}

func (w *World) AddDevice(d *Device) {
	w.mu.Lock()
	w.devices[d.ID] = d
	w.mu.Unlock()
	// attach the standard protocol handler to the device
	d.OnPacket = func(dev *Device, in *Iface, pkt *Packet) {
		w.handlePacket(dev, in, pkt)
	}
}

func (w *World) Connect(a *Iface, b *Iface) *Link {
	w.mu.Lock()
	defer w.mu.Unlock()
	w.seq++
	l := &Link{ID: fmt.Sprintf("L%d", w.seq), A: a, B: b, Delay: defaultDelay}
	w.links = append(w.links, l)
	a.Connected = b
	b.Connected = a
	a.State = LinkUp
	b.State = LinkUp
	return l
}

func (w *World) ConnectByName(d1 *Device, iname1 string, d2 *Device, iname2 string) (*Link, error) {
	i1 := d1.GetIface(iname1)
	if i1 == nil {
		return nil, fmt.Errorf("interface %s not found on %s", iname1, d1.Name)
	}
	i2 := d2.GetIface(iname2)
	if i2 == nil {
		return nil, fmt.Errorf("interface %s not found on %s", iname2, d2.Name)
	}
	return w.Connect(i1, i2), nil
}

func (w *World) PeerOf(iface *Iface) *Iface {
	return iface.Connected
}

// handlePacket is the device's receive path. It decides local delivery vs forward.
func (w *World) handlePacket(dev *Device, in *Iface, pkt *Packet) {
	// If the packet is addressed to this device's own IP, process locally.
	if local := dev.Net.FindInterfaceByIP(pkt.DstIP); local != nil {
		switch pkt.Type {
		case PktARPRequest:
			w.processARPRequest(dev, in, pkt)
		case PktARPReply:
			w.processARPReply(dev, in, pkt)
		case PktICMPEchoRequest:
			w.replyEcho(dev, in, pkt)
		case PktICMPEchoReply:
			w.signalPingReply(pkt)
		}
		return
	}

	// Not for us: learn ARP from source if direct, then forward.
	if pkt.SrcMac != ([6]byte{}) && in != nil && in.HasIP() {
		// only learn if source is on our directly connected subnet
		if pkt.SrcIP.Network(in.Mask) == in.Network() {
			dev.Net.ARP.Set(pkt.SrcIP, pkt.SrcMac, in, ARPTTL, w.Now())
		}
	}
	w.forward(dev, in, pkt)
}

func (w *World) processARPRequest(dev *Device, in *Iface, pkt *Packet) {
	w.logEvent(dev, in, pkt, "arp", fmt.Sprintf("ARP request: who has %s? tell %s", pkt.DstIP, pkt.SrcIP))
	// learn sender
	if in != nil && in.HasIP() {
		dev.Net.ARP.Set(pkt.SrcIP, pkt.SrcMac, in, ARPTTL, w.Now())
	}
	// send reply
	reply := &Packet{
		Type:    PktARPReply,
		SrcIP:   pkt.DstIP,
		DstIP:   pkt.SrcIP,
		SrcMac:  in.Mac,
		DstMac:  pkt.SrcMac,
		TTL:     DefaultTTL,
		Through: []string{},
	}
	w.logEvent(dev, in, reply, "arp", fmt.Sprintf("ARP reply: %s is-at %02x%02x.%02x%02x.%02x%02x", in.IP.String(), in.Mac[0], in.Mac[1], in.Mac[2], in.Mac[3], in.Mac[4], in.Mac[5]))
	w.sendVia(dev, in, reply)
}

func (w *World) processARPReply(dev *Device, in *Iface, pkt *Packet) {
	if in != nil && in.HasIP() {
		dev.Net.ARP.Set(pkt.SrcIP, pkt.SrcMac, in, ARPTTL, w.Now())
	}
	w.logEvent(dev, in, pkt, "arp", fmt.Sprintf("ARP learned: %s -> %02x%02x.%02x%02x.%02x%02x", pkt.SrcIP.String(), pkt.SrcMac[0], pkt.SrcMac[1], pkt.SrcMac[2], pkt.SrcMac[3], pkt.SrcMac[4], pkt.SrcMac[5]))
}

func (w *World) replyEcho(dev *Device, in *Iface, pkt *Packet) {
	w.logEvent(dev, in, pkt, "icmp", fmt.Sprintf("ICMP echo request from %s, replying", pkt.SrcIP))
	reply := &Packet{
		Type:    PktICMPEchoReply,
		SrcIP:   pkt.DstIP,
		DstIP:   pkt.SrcIP,
		SrcMac:  in.Mac,
		TTL:     DefaultTTL,
		Seq:     pkt.Seq,
		Payload: pkt.Payload,
		Through: []string{},
	}
	w.sendVia(dev, in, reply)
}

func (w *World) signalPingReply(pkt *Packet) {
	key := fmt.Sprintf("%s|%d", pkt.SrcIP.String(), pkt.Seq)
	w.mu.Lock()
	ch, ok := w.pending[key]
	w.mu.Unlock()
	if ok {
		select {
		case ch <- true:
		default:
		}
	}
}

// forward routes a packet to its next hop.
func (w *World) forward(dev *Device, in *Iface, pkt *Packet) {
	r := dev.Net.Routes.Lookup(pkt.DstIP)
	if r == nil {
		w.logEvent(dev, in, pkt, "drop", "No route to "+pkt.DstIP.String())
		return
	}

	out := r.Iface
	if out == nil || out.Connected == nil || out.State == LinkDown {
		w.logEvent(dev, in, pkt, "drop", "interface down")
		return
	}

	pkt.TTL--
	if pkt.TTL <= 0 {
		w.logEvent(dev, out, pkt, "drop", "TTL exceeded")
		return
	}

	pkt.Hops++
	pkt.Through = append(pkt.Through, dev.Name)
	w.logEvent(dev, out, pkt, "forward", fmt.Sprintf("%s -> %s", dev.Name, pkt.DstIP.String()))

	peerDev := w.deviceForIface(out.Connected)
	if peerDev == nil {
		w.logEvent(dev, out, pkt, "drop", "peer device not found")
		return
	}

	delay := defaultDelay
	if out.Connected != nil {
		_ = delay
	}
	time.Sleep(delay)
	if peerDev.OnPacket != nil {
		peerDev.OnPacket(peerDev, out.Connected, pkt)
	}
}

// sendVia sends a packet out of an interface (to the connected peer).
func (w *World) sendVia(dev *Device, out *Iface, pkt *Packet) {
	if out == nil || out.Connected == nil || out.State == LinkDown {
		w.logEvent(dev, out, pkt, "drop", "interface down")
		return
	}
	peerDev := w.deviceForIface(out.Connected)
	if peerDev == nil {
		return
	}
	time.Sleep(defaultDelay)
	if peerDev.OnPacket != nil {
		peerDev.OnPacket(peerDev, out.Connected, pkt)
	}
}

func (w *World) deviceForIface(iface *Iface) *Device {
	for _, d := range w.devices {
		for _, i := range d.Net.Interfaces {
			if i == iface {
				return d
			}
		}
	}
	return nil
}

func (w *World) logEvent(dev *Device, iface *Iface, pkt *Packet, action, detail string) {
	if w.onEvent != nil {
		w.onEvent(dev, iface, pkt, action, detail)
	}
}

// Ping sends `count` echo requests from src to dstIP, emitting status via onStatus.
func (w *World) Ping(src *Device, dstIP string, count int, onStatus func(timestamp string, seq int, ok bool, detail string)) {
	ip, err := ParseIP(dstIP)
	if err != nil {
		onStatus(w.Now().Format("15:04:05"), 0, false, "ping: invalid address "+dstIP)
		return
	}
	srcIP := w.SourceIPFor(src, ip)
	if srcIP.IsZero() {
		onStatus(w.Now().Format("15:04:05"), 0, false, "ping: no source address available")
		return
	}
	srcIface := src.Net.FindInterfaceByIP(srcIP)
	if srcIface == nil {
		onStatus(w.Now().Format("15:04:05"), 0, false, "ping: source interface not found")
		return
	}

	for i := 1; i <= count; i++ {
		seq := i
		key := fmt.Sprintf("%s|%d", ip.String(), seq)
		ch := make(chan bool, 1)
		w.mu.Lock()
		w.pending[key] = ch
		w.mu.Unlock()

		pkt := &Packet{
			Type:    PktICMPEchoRequest,
			SrcIP:   srcIP,
			DstIP:   ip,
			SrcMac:  srcIface.Mac,
			TTL:     DefaultTTL,
			Seq:     seq,
			Payload: "ping",
			Through: []string{},
		}

		// each probe: wait its turn, send, then wait for reply
		go func() {
			time.Sleep(time.Duration(seq-1) * 300 * time.Millisecond)
			w.forward(src, srcIface, pkt)
			select {
			case <-ch:
				onStatus(w.Now().Format("15:04:05"), seq, true, fmt.Sprintf("Reply from %s: bytes=56 Sequence=%d ttl=%d time=1 ms", ip.String(), seq, DefaultTTL))
			case <-time.After(800 * time.Millisecond):
				onStatus(w.Now().Format("15:04:05"), seq, false, fmt.Sprintf("Request time out, icmp_seq=%d", seq))
			}
			w.mu.Lock()
			delete(w.pending, key)
			w.mu.Unlock()
		}()
	}
}

// SourceIPFor picks the best source IP for reaching dst.
func (w *World) SourceIPFor(src *Device, dst IP) IP {
	for _, iface := range src.Net.Interfaces {
		if iface.HasIP() && dst.Network(iface.Mask) == iface.Network() {
			return iface.IP
		}
	}
	r := src.Net.Routes.Lookup(dst)
	if r != nil && r.Iface != nil {
		return r.Iface.IP
	}
	for _, iface := range src.Net.Interfaces {
		if iface.HasIP() && !iface.IsLoopback {
			return iface.IP
		}
	}
	return IP{}
}

func (w *World) Start() {
	log.Println("[world] simulation world created, waiting for traffic")
}