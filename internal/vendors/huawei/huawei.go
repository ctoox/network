package huawei

import (
	"fmt"
	"time"

	"netsim/internal/cli"
	"netsim/internal/net"
)

type VRP struct{}

func New() *VRP { return &VRP{} }

func (v *VRP) Name() string { return "VRP" }

func (v *VRP) VersionInfo() string {
	return "Huawei Versatile Routing Platform Software\nVRP (R) software, Version 5.130 (AR2200 V200R003C00)\nCopyright (C) 2011-2012 HUAWEI TECH CO., LTD"
}

func (v *VRP) NewSession(deviceID, deviceName string) *cli.Session {
	s := &cli.Session{
		DeviceID:   deviceID,
		DeviceName: deviceName,
		Vendor:     v,
	}
	// user view
	userTree := &cli.Cmd{}
	s.Push(&cli.View{
		ID: "user",
		Prompt: func(s *cli.Session) string {
			return "<" + s.DeviceName + ">"
		},
		Tree: userTree,
	})
	registerUserCommands(s, userTree)
	return s
}

func registerUserCommands(s *cli.Session, tree *cli.Cmd) {
	tree.Add(&cli.Cmd{Name: "system-view", Help: "Enter system view, return user view with Ctrl+Z.", Handler: func(s *cli.Session, args []string) error {
		s.Printf("Enter system view, return user view with Ctrl+Z.\n")
		s.Push(systemView(s))
		return nil
	}})
	tree.Add(&cli.Cmd{Name: "ping", Help: "Ping a remote host.", MinArgs: 1, Handler: pingHandler(s, true)})
	tree.Add(&cli.Cmd{Name: "display", Children: []*cli.Cmd{
		{Name: "version", Help: "Display version information.", Handler: func(s *cli.Session, args []string) error {
			s.Println(s.Vendor.VersionInfo())
			return nil
		}},
		{Name: "current-configuration", Help: "Display current configuration.", Handler: displayCurrentConfig(s)},
		{Name: "ip", Children: []*cli.Cmd{
			{Name: "routing-table", Help: "Display IP routing table.", Handler: displayRoutingTable(s)},
		}},
		{Name: "arp", Help: "Display ARP table.", Handler: displayARP(s)},
		{Name: "interface", Help: "Display interface information.", MinArgs: 1, Handler: displayInterface(s)},
		{Name: "brief", Help: "Display interface brief.", Handler: displayBrief(s)},
	}})
	tree.Add(&cli.Cmd{Name: "tracert", Help: "Trace route to a remote host.", MinArgs: 1, Handler: tracertHandler(s)})
	tree.Add(&cli.Cmd{Name: "quit", Help: "Exit.", Handler: func(s *cli.Session, args []string) error {
		s.Printf("  quit\n")
		return nil
	}})
}

func systemView(s *cli.Session) *cli.View {
	tree := &cli.Cmd{}
	tree.Add(&cli.Cmd{Name: "sysname", Help: "Configure system name.", MinArgs: 1, Handler: func(s *cli.Session, args []string) error {
		name := args[0]
		s.DeviceName = name
		if s.Dev != nil {
			s.Dev.Name = name
		}
		return nil
	}})
	tree.Add(&cli.Cmd{Name: "interface", Help: "Enter interface view.", MinArgs: 1, Handler: func(s *cli.Session, args []string) error {
		name := args[0]
		if s.Dev != nil && s.Dev.GetIface(name) == nil {
			s.Printf("  Error: Interface %s does not exist.\n", name)
			return nil
		}
		s.Printf("Enter system view, return user view with Ctrl+Z.\n")
		s.Printf("[%s]interface %s\n", s.DeviceName, name)
		s.Push(interfaceView(name))
		return nil
	}})
	tree.Add(&cli.Cmd{Name: "ip", Children: []*cli.Cmd{
		{Name: "route-static", Help: "Configure static route.", MinArgs: 2, Handler: staticRouteHandler(s)},
	}})
	tree.Add(&cli.Cmd{Name: "ospf", Help: "Enter OSPF view.", Handler: func(s *cli.Session, args []string) error {
		s.Printf("  OSPF is not yet supported in this build.\n")
		return nil
	}})
	tree.Add(&cli.Cmd{Name: "return", Help: "Return to user view.", Handler: func(s *cli.Session, args []string) error {
		s.Return()
		return nil
	}})
	tree.Add(&cli.Cmd{Name: "quit", Help: "Return to user view.", Handler: func(s *cli.Session, args []string) error {
		s.Pop()
		return nil
	}})
	tree.Add(&cli.Cmd{Name: "display", Children: []*cli.Cmd{
		{Name: "current-configuration", Help: "Display current configuration.", Handler: displayCurrentConfig(s)},
		{Name: "ip", Children: []*cli.Cmd{
			{Name: "routing-table", Help: "Display IP routing table.", Handler: displayRoutingTable(s)},
		}},
		{Name: "arp", Help: "Display ARP table.", Handler: displayARP(s)},
		{Name: "interface", Help: "Display interface information.", MinArgs: 1, Handler: displayInterface(s)},
		{Name: "brief", Help: "Display interface brief.", Handler: displayBrief(s)},
		{Name: "version", Help: "Display version information.", Handler: func(s *cli.Session, args []string) error {
			s.Println(s.Vendor.VersionInfo())
			return nil
		}},
	}})
	tree.Add(&cli.Cmd{Name: "ping", Help: "Ping a remote host.", MinArgs: 1, Handler: pingHandler(s, true)})
	return &cli.View{
		ID: "system",
		Prompt: func(s *cli.Session) string {
			return "[" + s.DeviceName + "]"
		},
		Tree: tree,
	}
}

func interfaceView(name string) *cli.View {
	tree := &cli.Cmd{}
	tree.Add(&cli.Cmd{Name: "ip", Children: []*cli.Cmd{
		{Name: "address", Help: "Configure interface IP address.", MinArgs: 1, Handler: ipAddressHandler(name)},
	}})
	tree.Add(&cli.Cmd{Name: "quit", Help: "Return to system view.", Handler: func(s *cli.Session, args []string) error {
		s.Pop()
		return nil
	}})
	tree.Add(&cli.Cmd{Name: "return", Help: "Return to user view.", Handler: func(s *cli.Session, args []string) error {
		s.Return()
		return nil
	}})
	tree.Add(&cli.Cmd{Name: "display", Children: []*cli.Cmd{
		{Name: "this", Help: "Display this interface config.", Handler: func(s *cli.Session, args []string) error {
			iface := s.Dev.GetIface(name)
			s.Printf("interface %s\n", iface.Name)
			if iface.HasIP() {
				s.Printf(" ip address %s %s\n", iface.IP.String(), iface.Mask.String())
			}
			return nil
		}},
	}})
	tree.Add(&cli.Cmd{Name: "shutdown", Help: "Shutdown interface.", Handler: func(s *cli.Session, args []string) error {
		iface := s.Dev.GetIface(name)
		iface.State = net.LinkDown
		s.Printf("  Interface %s has been shut down.\n", name)
		return nil
	}})
	tree.Add(&cli.Cmd{Name: "undo", Children: []*cli.Cmd{
		{Name: "shutdown", Help: "Undo shutdown.", Handler: func(s *cli.Session, args []string) error {
			iface := s.Dev.GetIface(name)
			iface.State = net.LinkUp
			s.Printf("  Interface %s has been brought up.\n", name)
			return nil
		}},
		{Name: "ip", Children: []*cli.Cmd{
			{Name: "address", Help: "Remove interface IP.", Handler: func(s *cli.Session, args []string) error {
				iface := s.Dev.GetIface(name)
				iface.IP = net.IP{}
				iface.Mask = net.Mask{}
				return nil
			}},
		}},
	}})
	return &cli.View{
		ID: "interface",
		Prompt: func(s *cli.Session) string {
			return "[" + s.DeviceName + "-" + name + "]"
		},
		Tree: tree,
	}
}

// handlers

func pingHandler(s *cli.Session, _ bool) func(*cli.Session, []string) error {
	return func(s *cli.Session, args []string) error {
		dst := args[0]
		count := 5
		if len(args) >= 3 && args[1] == "-c" {
			fmt.Sscanf(args[2], "%d", &count)
		}
		if s.World == nil || s.Dev == nil {
			s.Printf("  ping: no simulation attached\n")
			return nil
		}
		s.Printf("  PING %s: 56 data bytes, press CTRL_C to break\n", dst)
		received := 0
		s.World.Ping(s.Dev, dst, count, func(ts string, seq int, ok bool, detail string) {
			s.Println("  " + detail)
			if ok {
				received++
			}
		})
		time.Sleep(time.Duration(count)*350*time.Millisecond + 1200*time.Millisecond)
		loss := 0.0
		if count > 0 {
			loss = float64(count-received) / float64(count) * 100
		}
		s.Printf("  --- %s ping statistics ---\n", dst)
		s.Printf("  %d packet(s) transmitted, %d packet(s) received, %.1f%% packet loss\n", count, received, loss)
		return nil
	}
}

func displayRoutingTable(s *cli.Session) func(*cli.Session, []string) error {
	return func(s *cli.Session, args []string) error {
		if s.Dev == nil {
			s.Printf("  no device\n")
			return nil
		}
		s.Printf("Route Flags: R - relay, D - download to fib\n")
		s.Printf("------------------------------------------------------------------------------\n")
		s.Printf("Routing Tables:\n")
		s.Printf("         Destination/Mask    Proto   Pre  Cost      Flags NextHop         Interface\n")
		for _, r := range s.Dev.Net.Routes.Sorted() {
			proto := map[string]string{"S": "Static", "D": "Direct", "O": "OSPF", "B": "BGP"}[r.Proto]
			if proto == "" {
				proto = r.Proto
			}
			pre := 0
			if r.Proto == "S" {
				pre = 60
			} else if r.Proto == "D" {
				pre = 0
			}
			nextHop := "Direct"
			if !r.NextHop.IsZero() {
				nextHop = r.NextHop.String()
			}
			iface := ""
			if r.Iface != nil {
				iface = r.Iface.Name
			}
			s.Printf("%19s/%d    %-6s %3d  %3d      R     %-15s %s\n",
				r.Prefix.String(), r.Mask.PrefixLen(), proto, pre, r.Metric, nextHop, iface)
		}
		s.Printf("\n")
		return nil
	}
}

func displayARP(s *cli.Session) func(*cli.Session, []string) error {
	return func(s *cli.Session, args []string) error {
		if s.Dev == nil {
			return nil
		}
		s.Println(s.Dev.Net.ARP.String())
		return nil
	}
}

func displayCurrentConfig(s *cli.Session) func(*cli.Session, []string) error {
	return func(s *cli.Session, args []string) error {
		if s.Dev == nil {
			return nil
		}
		s.Printf("#\n")
		s.Printf("sysname %s\n", s.Dev.Name)
		s.Printf("#\n")
		for _, iface := range s.Dev.Net.Interfaces {
			if iface.IsLoopback {
				continue
			}
			s.Printf("interface %s\n", iface.Name)
			if iface.HasIP() {
				s.Printf(" ip address %s %s\n", iface.IP.String(), iface.Mask.String())
			}
			if iface.State == net.LinkDown {
				s.Printf(" shutdown\n")
			}
			s.Printf("#\n")
		}
		for _, r := range s.Dev.Net.Routes.Sorted() {
			if r.Proto == "S" {
				s.Printf("ip route-static %s %d %s\n", r.Prefix.String(), r.Mask.PrefixLen(), r.NextHop.String())
			}
		}
		s.Printf("return\n")
		return nil
	}
}

func displayInterface(s *cli.Session) func(*cli.Session, []string) error {
	return func(s *cli.Session, args []string) error {
		name := args[0]
		iface := s.Dev.GetIface(name)
		if iface == nil {
			s.Printf("  Error: Interface %s does not exist.\n", name)
			return nil
		}
		state := "down"
		protocol := "down"
		if iface.State == net.LinkUp && iface.HasIP() {
			state = "up"
			protocol = "up"
		}
		s.Printf("%s current state : %s\n", iface.Name, state)
		s.Printf("Line protocol current state : %s\n", protocol)
		if iface.HasIP() {
			s.Printf("Internet Address is %s/%d\n", iface.IP.String(), iface.Mask.PrefixLen())
		} else {
			s.Printf("Internet Address is unnumbered\n")
		}
		return nil
	}
}

func displayBrief(s *cli.Session) func(*cli.Session, []string) error {
	return func(s *cli.Session, args []string) error {
		s.Printf("Interface                         IP Address/Mask      Physical   Protocol  \n")
		for _, iface := range s.Dev.Net.Interfaces {
			phy := "down"
			proto := "down"
			if iface.State == net.LinkUp && iface.HasIP() {
				phy = "up"
				proto = "up"
			}
			ip := "unassigned"
			if iface.HasIP() {
				ip = fmt.Sprintf("%s/%d", iface.IP.String(), iface.Mask.PrefixLen())
			}
			s.Printf("%-31s %-20s %s  %s\n", iface.Name, ip, phy, proto)
		}
		return nil
	}
}

func staticRouteHandler(s *cli.Session) func(*cli.Session, []string) error {
	return func(s *cli.Session, args []string) error {
		prefix, err := net.ParseIP(args[0])
		if err != nil {
			s.Printf("  Error: invalid prefix.\n")
			return nil
		}
		mask, err := net.ParseMask(args[1])
		if err != nil {
			s.Printf("  Error: invalid mask.\n")
			return nil
		}
		nh, err := net.ParseIP(args[2])
		if err != nil {
			s.Printf("  Error: invalid next-hop.\n")
			return nil
		}
		// find outgoing interface via direct route to next-hop
		var iface *net.Iface
		for _, i := range s.Dev.Net.Interfaces {
			if i.HasIP() && !i.IsLoopback && nh.Network(i.Mask) == i.Network() {
				iface = i
				break
			}
		}
		r := &net.Route{Prefix: prefix.Network(mask), Mask: mask, NextHop: nh, Iface: iface, Proto: "S", Metric: 1}
		s.Dev.Net.Routes.Add(r)
		return nil
	}
}

func ipAddressHandler(name string) func(*cli.Session, []string) error {
	return func(s *cli.Session, args []string) error {
		ip, err := net.ParseIP(args[0])
		if err != nil {
			s.Printf("  Error: invalid IP address.\n")
			return nil
		}
		mask, err := net.ParseMask(args[1])
		if err != nil {
			s.Printf("  Error: invalid mask.\n")
			return nil
		}
		iface := s.Dev.GetIface(name)
		if iface == nil {
			s.Printf("  Error: interface %s does not exist.\n", name)
			return nil
		}
		iface.IP = ip
		iface.Mask = mask
		s.Dev.Net.Routes.Add(&net.Route{
			Prefix: ip.Network(mask), Mask: mask, Iface: iface, Proto: "D", Metric: 0,
		})
		return nil
	}
}

func tracertHandler(s *cli.Session) func(*cli.Session, []string) error {
	return func(s *cli.Session, args []string) error {
		s.Printf("  tracert: not yet implemented\n")
		return nil
	}
}