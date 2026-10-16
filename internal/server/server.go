package server

import (
	"encoding/json"
	"log"
	"net/http"
	"sync"

	"netsim/internal/cli"
	"netsim/internal/net"
	"netsim/internal/vendors/cisco"
	"netsim/internal/vendors/huawei"

	"github.com/gorilla/websocket"
)

type Server struct {
	world *net.World
	up    websocket.Upgrader
	mu    sync.Mutex
	sess  map[string]*cli.Session
}

func New(world *net.World) *Server {
	return &Server{
		world: world,
		up: websocket.Upgrader{
			CheckOrigin: func(r *http.Request) bool { return true },
		},
		sess: map[string]*cli.Session{},
	}
}

// buildDemoTopology creates a mixed Huawei+Cisco network for the demo.
func BuildDemoTopology(w *net.World) {
	// Host1 (PC) -- Huawei R1 -- Cisco R2 -- Host2 (PC)
	host1 := net.NewDevice("host1", "PC1", net.TypeHost, net.VendorHuawei)
	h1i := &net.Iface{Name: "Ethernet0/0/0", Mac: mac("00e0.fc00.0001")}
	host1.AddIface(h1i)

	r1 := net.NewDevice("r1", "AR1", net.TypeRouter, net.VendorHuawei)
	r1ge0 := &net.Iface{Name: "GE0/0/0", Mac: mac("00e0.fc00.0011")}
	r1ge1 := &net.Iface{Name: "GE0/0/1", Mac: mac("00e0.fc00.0012")}
	r1.AddIface(r1ge0)
	r1.AddIface(r1ge1)

	r2 := net.NewDevice("r2", "R2", net.TypeRouter, net.VendorCisco)
	r2ge0 := &net.Iface{Name: "GigabitEthernet0/0", Mac: mac("0050.1400.0021")}
	r2ge1 := &net.Iface{Name: "GigabitEthernet0/1", Mac: mac("0050.1400.0022")}
	r2.AddIface(r2ge0)
	r2.AddIface(r2ge1)

	host2 := net.NewDevice("host2", "PC2", net.TypeHost, net.VendorCisco)
	h2i := &net.Iface{Name: "Ethernet0/0/0", Mac: mac("0050.1400.0031")}
	host2.AddIface(h2i)

	w.AddDevice(host1)
	w.AddDevice(r1)
	w.AddDevice(r2)
	w.AddDevice(host2)

	w.Connect(h1i, r1ge0)
	w.Connect(r1ge1, r2ge0)
	w.Connect(r2ge1, h2i)

	// Pre-seed addresses and routes so the demo works immediately.
	// PC1
	h1i.IP = mustIP("192.168.1.1")
	h1i.Mask = net.MaskFromLen(24)
	host1.Net.Routes.Add(&net.Route{Prefix: net.IP{192, 168, 1, 0}, Mask: net.MaskFromLen(24), Iface: h1i, Proto: "D"})
	host1.Net.Routes.Add(&net.Route{Prefix: net.IP{192, 168, 2, 0}, Mask: net.MaskFromLen(24), NextHop: net.IP{192, 168, 1, 254}, Iface: h1i, Proto: "S", Metric: 1})

	// AR1 (Huawei)
	r1ge0.IP = mustIP("192.168.1.254")
	r1ge0.Mask = net.MaskFromLen(24)
	r1ge1.IP = mustIP("10.0.0.1")
	r1ge1.Mask = net.MaskFromLen(30)
	r1.Net.Routes.Add(&net.Route{Prefix: net.IP{192, 168, 1, 0}, Mask: net.MaskFromLen(24), Iface: r1ge0, Proto: "D"})
	r1.Net.Routes.Add(&net.Route{Prefix: net.IP{10, 0, 0, 0}, Mask: net.MaskFromLen(30), Iface: r1ge1, Proto: "D"})
	r1.Net.Routes.Add(&net.Route{Prefix: net.IP{192, 168, 2, 0}, Mask: net.MaskFromLen(24), NextHop: net.IP{10, 0, 0, 2}, Iface: r1ge1, Proto: "S", Metric: 1})

	// R2 (Cisco)
	r2ge0.IP = mustIP("10.0.0.2")
	r2ge0.Mask = net.MaskFromLen(30)
	r2ge1.IP = mustIP("192.168.2.254")
	r2ge1.Mask = net.MaskFromLen(24)
	r2.Net.Routes.Add(&net.Route{Prefix: net.IP{10, 0, 0, 0}, Mask: net.MaskFromLen(30), Iface: r2ge0, Proto: "D"})
	r2.Net.Routes.Add(&net.Route{Prefix: net.IP{192, 168, 2, 0}, Mask: net.MaskFromLen(24), Iface: r2ge1, Proto: "D"})
	r2.Net.Routes.Add(&net.Route{Prefix: net.IP{192, 168, 1, 0}, Mask: net.MaskFromLen(24), NextHop: net.IP{10, 0, 0, 1}, Iface: r2ge0, Proto: "S", Metric: 1})

	// PC2
	h2i.IP = mustIP("192.168.2.1")
	h2i.Mask = net.MaskFromLen(24)
	host2.Net.Routes.Add(&net.Route{Prefix: net.IP{192, 168, 2, 0}, Mask: net.MaskFromLen(24), Iface: h2i, Proto: "D"})
	host2.Net.Routes.Add(&net.Route{Prefix: net.IP{192, 168, 1, 0}, Mask: net.MaskFromLen(24), NextHop: net.IP{192, 168, 2, 254}, Iface: h2i, Proto: "S", Metric: 1})
}

func mac(s string) [6]byte {
	var m [6]byte
	var h [6]int
	_, _ = fmtSscanf(s, &h)
	for i := 0; i < 6; i++ {
		m[i] = byte(h[i])
	}
	return m
}

func mustIP(s string) net.IP {
	ip, _ := net.ParseIP(s)
	return ip
}

func fmtSscanf(s string, h *[6]int) (int, error) {
	// simple "aabb.ccdd.eeff" -> bytes
	n := 0
	idx := 0
	for _, c := range s {
		if c == '.' || c == ':' {
			continue
		}
		if c >= '0' && c <= '9' {
			h[idx] = h[idx]*16 + int(c-'0')
		} else if c >= 'a' && c <= 'f' {
			h[idx] = h[idx]*16 + int(c-'a'+10)
		} else if c >= 'A' && c <= 'F' {
			h[idx] = h[idx]*16 + int(c-'A'+10)
		}
		n++
		if n%2 == 0 {
			idx++
		}
	}
	return idx, nil
}

// HandleWebSocket upgrades HTTP to websocket and serves the CLI terminal.
func (s *Server) HandleWebSocket(w http.ResponseWriter, r *http.Request) {
	conn, err := s.up.Upgrade(w, r, nil)
	if err != nil {
		log.Printf("ws upgrade: %v", err)
		return
	}
	defer conn.Close()

	// first message should be {"cmd":"connect","device":"r1"}
	var init struct {
		Cmd    string `json:"cmd"`
		Device string `json:"device"`
	}
	if err := conn.ReadJSON(&init); err != nil {
		return
	}
	if init.Cmd != "connect" {
		conn.WriteJSON(map[string]string{"type": "error", "text": "expected connect"})
		return
	}

	dev := s.world.GetDevice(init.Device)
	if dev == nil {
		conn.WriteJSON(map[string]string{"type": "error", "text": "device not found"})
		return
	}

	var vendor cli.Vendor
	switch dev.Vendor {
	case net.VendorHuawei:
		vendor = huawei.New()
	case net.VendorCisco:
		vendor = cisco.New()
	default:
		conn.WriteJSON(map[string]string{"type": "error", "text": "unsupported vendor"})
		return
	}

	sess := vendor.NewSession(dev.ID, dev.Name)
	sess.Dev = dev
	sess.World = s.world

	// banner
	sess.Println(vendor.Name() + " simulator connected to " + dev.Name)
	sess.Printf("\n")
	sess.Printf("Device: %s  Type: %s  Vendor: %s\n", dev.Name, dev.Type.String(), dev.Vendor.String())
	sess.Printf("Type '?' for command help.\n")

	conn.WriteJSON(map[string]interface{}{
		"type":   "banner",
		"text":   sess.Output.String(),
		"prompt": sess.Prompt(),
	})
	sess.Output.Reset()

	// command loop
	for {
		var msg struct {
			Cmd  string `json:"cmd"`
			Line string `json:"line"`
		}
		if err := conn.ReadJSON(&msg); err != nil {
			return
		}
		if msg.Cmd == "exec" {
			out, _ := sess.Execute(msg.Line)
			conn.WriteJSON(map[string]interface{}{
				"type":   "exec",
				"text":   out,
				"prompt": sess.Prompt(),
			})
		} else if msg.Cmd == "tab" {
			// tab completion: expand last token
			conn.WriteJSON(map[string]interface{}{"type": "tab", "text": sess.TabExpand(msg.Line)})
		}
	}
}

// HandleTopology returns the current topology as JSON.
func (s *Server) HandleTopology(w http.ResponseWriter, r *http.Request) {
	devs := s.world.Devices()
	type nodeJSON struct {
		ID      string   `json:"id"`
		Name    string   `json:"name"`
		Type    string   `json:"type"`
		Vendor  string   `json:"vendor"`
		Ifaces  []string `json:"ifaces"`
		IPs     []string `json:"ips"`
	}
	nodes := make([]nodeJSON, 0, len(devs))
	for _, d := range devs {
		n := nodeJSON{ID: d.ID, Name: d.Name, Type: d.Type.String(), Vendor: d.Vendor.String()}
		for _, i := range d.Net.Interfaces {
			n.Ifaces = append(n.Ifaces, i.Name)
			if i.HasIP() {
				n.IPs = append(n.IPs, i.IP.String()+"/"+fmtInt(i.Mask.PrefixLen()))
			} else {
				n.IPs = append(n.IPs, "")
			}
		}
		nodes = append(nodes, n)
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{"nodes": nodes})
}

func fmtInt(n int) string {
	if n == 0 {
		return "0"
	}
	neg := n < 0
	if neg {
		n = -n
	}
	var b []byte
	for n > 0 {
		b = append([]byte{byte('0' + n%10)}, b...)
		n /= 10
	}
	if neg {
		return "-" + string(b)
	}
	return string(b)
}