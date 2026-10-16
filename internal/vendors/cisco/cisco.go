package cisco

import (
	"fmt"
	"strings"
	"time"

	"netsim/internal/cli"
	"netsim/internal/net"
)

type IOS struct{}

func New() *IOS { return &IOS{} }

func (v *IOS) Name() string { return "IOS" }

func (v *IOS) VersionInfo() string {
	return "Cisco IOS Software, C7200 Software (C7200-ADVENTERPRISEK9-M), Version 15.2(4)M7, RELEASE SOFTWARE\nTechnical Support: http://www.cisco.com/techsupport"
}

func (v *IOS) NewSession(deviceID, deviceName string) *cli.Session {
	s := &cli.Session{
		DeviceID:   deviceID,
		DeviceName: deviceName,
		Vendor:     v,
	}
	// user exec mode
	userTree := &cli.Cmd{}
	s.Push(&cli.View{
		ID: "user",
		Prompt: func(s *cli.Session) string {
			return s.DeviceName + ">"
		},
		Tree: userTree,
	})
	registerUserCommands(s, userTree)
	return s
}

func registerUserCommands(s *cli.Session, tree *cli.Cmd) {
	tree.Add(&cli.Cmd{Name: "enable", Help: "Turn on privileged commands.", Handler: func(s *cli.Session, args []string) error {
		s.Push(privView(s))
		return nil
	}})
	tree.Add(&cli.Cmd{Name: "ping", Help: "Send echo messages.", MinArgs: 1, Handler: pingHandler(s)})
	tree.Add(&cli.Cmd{Name: "show", Aliases: []string{"sh"}, Children: []*cli.Cmd{
		{Name: "version", Help: "Show version information.", Handler: func(s *cli.Session, args []string) error {
			s.Println(s.Vendor.VersionInfo())
			return nil
		}},
		{Name: "ip", Children: []*cli.Cmd{
			{Name: "route", Help: "Show IP routing table.", Handler: displayRoutingTable(s)},
			{Name: "interface", Help: "Show interface status.", Handler: displayBrief(s)},
		}},
		{Name: "arp", Help: "Show ARP table.", Handler: displayARP(s)},
		{Name: "interfaces", Help: "Show interface details.", MinArgs: 1, Handler: displayInterface(s)},
		{Name: "running-config", Help: "Show running configuration.", Handler: displayRunningConfig(s)},
	}})
	tree.Add(&cli.Cmd{Name: "traceroute", Aliases: []string{"trace"}, Help: "Trace route to destination.", MinArgs: 1, Handler: func(s *cli.Session, args []string) error {
		s.Printf("  traceroute: not yet implemented\n")
		return nil
	}})
	tree.Add(&cli.Cmd{Name: "quit", Help: "Exit.", Handler: func(s *cli.Session, args []string) error {
		s.Printf("  Connection closed by foreign host.\n")
		return nil
	}})
}

func privView(s *cli.Session) *cli.View {
	tree := &cli.Cmd{}
	tree.Add(&cli.Cmd{Name: "configure", Aliases: []string{"conf"}, Help: "Enter configuration mode.", Children: []*cli.Cmd{
		{Name: "terminal", Aliases: []string{"t"}, Help: "Configure from terminal.", Handler: func(s *cli.Session, args []string) error {
			s.Printf("Enter configuration commands, one per line.  End with CNTL/Z.\n")
			s.Push(configView(s))
			return nil
		}},
	}})
	tree.Add(&cli.Cmd{Name: "show", Aliases: []string{"sh"}, Children: []*cli.Cmd{
		{Name: "running-config", Help: "Show running configuration.", Handler: displayRunningConfig(s)},
		{Name: "ip", Children: []*cli.Cmd{
			{Name: "route", Help: "Show IP routing table.", Handler: displayRoutingTable(s)},
			{Name: "interface", Help: "Show interface status.", Handler: displayBrief(s)},
		}},
		{Name: "arp", Help: "Show ARP table.", Handler: displayARP(s)},
		{Name: "version", Help: "Show version information.", Handler: func(s *cli.Session, args []string) error {
			s.Println(s.Vendor.VersionInfo())
			return nil
		}},
	}})
	tree.Add(&cli.Cmd{Name: "ping", Help: "Send echo messages.", MinArgs: 1, Handler: pingHandler(s)})
	tree.Add(&cli.Cmd{Name: "disable", Help: "Turn off privileged commands.", Handler: func(s *cli.Session, args []string) error {
		s.Pop()
		return nil
	}})
	return &cli.View{
		ID: "privileged",
		Prompt: func(s *cli.Session) string {
			return s.DeviceName + "#"
		},
		Tree: tree,
	}
}

func configView(s *cli.Session) *cli.View {
	tree := &cli.Cmd{}
	tree.Add(&cli.Cmd{Name: "hostname", Help: "Set the system hostname.", MinArgs: 1, Handler: func(s *cli.Session, args []string) error {
		name := args[0]
		s.DeviceName = name
		if s.Dev != nil {
			s.Dev.Name = name
		}
		return nil
	}})
	tree.Add(&cli.Cmd{Name: "interface", Help: "Enter interface configuration mode.", MinArgs: 1, Handler: func(s *cli.Session, args []string) error {
		name := args[0]
		if s.Dev != nil && s.Dev.GetIface(name) == nil {
			s.Printf("  %s\n", "Invalid input detected")
			return nil
		}
		s.Push(ifaceConfigView(name))
		return nil
	}})
	tree.Add(&cli.Cmd{Name: "ip", Children: []*cli.Cmd{
		{Name: "route", Help: "Establish static routes.", MinArgs: 3, Handler: staticRouteHandler(s)},
	}})
	tree.Add(&cli.Cmd{Name: "end", Help: "Exit to privileged mode.", Handler: func(s *cli.Session, args []string) error {
		s.Return()
		return nil
	}})
	tree.Add(&cli.Cmd{Name: "exit", Help: "Exit to privileged mode.", Handler: func(s *cli.Session, args []string) error {
		s.Pop()
		return nil
	}})
	tree.Add(&cli.Cmd{Name: "do", Help: "Run a privileged command from config mode.", MinArgs: 1, Handler: func(s *cli.Session, args []string) error {
		s.RunAs(privView(s).Tree, strings.Join(args, " "))
		return nil
	}})
	return &cli.View{
		ID: "config",
		Prompt: func(s *cli.Session) string {
			return "Router(config)#"
		},
		Tree: tree,
	}
}

func ifaceConfigView(name string) *cli.View {
	tree := &cli.Cmd{}
	tree.Add(&cli.Cmd{Name: "ip", Children: []*cli.Cmd{
		{Name: "address", Help: "Set interface IP address.", MinArgs: 2, Handler: ipAddressHandler(name)},
	}})
	tree.Add(&cli.Cmd{Name: "no", Children: []*cli.Cmd{
		{Name: "shutdown", Help: "Bring interface up.", Handler: func(s *cli.Session, args []string) error {
			iface := s.Dev.GetIface(name)
			iface.State = net.LinkUp
			s.Printf("  %s\n", "%LINK-5-CHANGED: Interface "+name+", changed state to up")
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
	tree.Add(&cli.Cmd{Name: "shutdown", Help: "Shutdown interface.", Handler: func(s *cli.Session, args []string) error {
		iface := s.Dev.GetIface(name)
		iface.State = net.LinkDown
		s.Printf("  %s\n", "%LINK-5-CHANGED: Interface "+name+", changed state to administratively down")
		return nil
	}})
	tree.Add(&cli.Cmd{Name: "exit", Help: "Return to global config mode.", Handler: func(s *cli.Session, args []string) error {
		s.Pop()
		return nil
	}})
	tree.Add(&cli.Cmd{Name: "end", Help: "Exit to privileged mode.", Handler: func(s *cli.Session, args []string) error {
		s.Return()
		return nil
	}})
	tree.Add(&cli.Cmd{Name: "do", Help: "Run a privileged command.", MinArgs: 1, Handler: func(s *cli.Session, args []string) error {
		s.RunAs(privView(s).Tree, strings.Join(args, " "))
		return nil
	}})
	tree.Add(&cli.Cmd{Name: "description", Help: "Set interface description.", MinArgs: 1, Handler: func(s *cli.Session, args []string) error {
		return nil
	}})
	return &cli.View{
		ID: "iface-config",
		Prompt: func(s *cli.Session) string {
			return "Router(config-if)#"
		},
		Tree: tree,
	}
}

// handlers

func pingHandler(s *cli.Session) func(*cli.Session, []string) error {
	return func(s *cli.Session, args []string) error {
		dst := args[0]
		count := 5
		if s.World == nil || s.Dev == nil {
			s.Printf("  ping: no simulation attached\n")
			return nil
		}
		s.Printf("Type escape sequence to abort.\n")
		s.Printf("Sending %d, 100-byte ICMP Echos to %s, timeout is 2 seconds:\n", count, dst)
		received := 0
		s.World.Ping(s.Dev, dst, count, func(ts string, seq int, ok bool, detail string) {
			if ok {
				s.Printf("!!!!!\n")
				received++
			} else {
				s.Printf(".....\n")
			}
		})
		time.Sleep(time.Duration(count)*350*time.Millisecond + 1200*time.Millisecond)
		loss := 0.0
		if count > 0 {
			loss = float64(count-received) / float64(count) * 100
		}
		s.Printf("Success rate is %.0f percent (%d/%d)\n", 100-loss, received, count)
		return nil
	}
}

func displayRoutingTable(s *cli.Session) func(*cli.Session, []string) error {
	return func(s *cli.Session, args []string) error {
		s.Printf("Codes: C - connected, S - static, I - IGRP, R - RIP, M - mobile, B - BGP\n")
		s.Printf("       D - EIGRP, EX - EIGRP external, O - OSPF, IA - OSPF inter area\n")
		s.Printf("       E1 - OSPF external type 1, E2 - OSPF external type 2\n")
		s.Printf("       i - IS-IS, su - IS-IS summary, L1 - IS-IS level-1, L2 - IS-IS level-2\n")
		s.Printf("       ia - IS-IS inter area, * - candidate default, U - per-user static route\n")
		for _, r := range s.Dev.Net.Routes.Sorted() {
			code := map[string]string{"S": "S", "D": "C", "O": "O", "B": "B"}[r.Proto]
			if code == "" {
				code = "?"
			}
			via := "is directly connected"
			viaIface := ""
			if r.Iface != nil {
				viaIface = r.Iface.Name
			}
			if !r.NextHop.IsZero() {
				via = fmt.Sprintf("via %s", r.NextHop.String())
			}
			s.Printf("%2s    %s/%d [%d/%d] %s, %s\n", code, r.Prefix.String(), r.Mask.PrefixLen(), r.Metric, r.Metric, via, viaIface)
		}
		s.Printf("\n")
		return nil
	}
}

func displayARP(s *cli.Session) func(*cli.Session, []string) error {
	return func(s *cli.Session, args []string) error {
		s.Printf("Protocol  Address          Age (min)  Hardware Addr   Type   Interface\n")
		for _, e := range s.Dev.Net.ARP.All() {
			s.Printf("Internet  %-15s %6d  %02x%02x.%02x%02x.%02x%02x  ARPA  %s\n",
				e.IP.String(), 0, e.Mac[0], e.Mac[1], e.Mac[2], e.Mac[3], e.Mac[4], e.Mac[5], e.Iface.Name)
		}
		return nil
	}
}

func displayBrief(s *cli.Session) func(*cli.Session, []string) error {
	return func(s *cli.Session, args []string) error {
		s.Printf("Interface                  IP-Address      OK? Method Status                Protocol\n")
		for _, iface := range s.Dev.Net.Interfaces {
			status := "administratively down"
			proto := "down"
			if iface.State == net.LinkUp && iface.HasIP() {
				status = "up"
				proto = "up"
			}
			ip := "unassigned"
			if iface.HasIP() {
				ip = iface.IP.String()
			}
			s.Printf("%-25s %-15s YES NVRAM %s                  %s\n", iface.Name, ip, status, proto)
		}
		return nil
	}
}

func displayInterface(s *cli.Session) func(*cli.Session, []string) error {
	return func(s *cli.Session, args []string) error {
		name := args[0]
		iface := s.Dev.GetIface(name)
		if iface == nil {
			s.Printf("  %s\n", "Invalid input detected")
			return nil
		}
		line := "down"
		if iface.State == net.LinkUp {
			line = "up"
		}
		s.Printf("%s is %s, line protocol is %s\n", iface.Name, line, line)
		if iface.HasIP() {
			s.Printf("  Internet address is %s/%d\n", iface.IP.String(), iface.Mask.PrefixLen())
		}
		return nil
	}
}

func displayRunningConfig(s *cli.Session) func(*cli.Session, []string) error {
	return func(s *cli.Session, args []string) error {
		s.Printf("Building configuration...\n\n")
		s.Printf("Current configuration : 0 bytes\n")
		s.Printf("!\n")
		s.Printf("version 15.2\n")
		s.Printf("hostname %s\n", s.Dev.Name)
		s.Printf("!\n")
		for _, iface := range s.Dev.Net.Interfaces {
			if iface.IsLoopback {
				continue
			}
			s.Printf("interface %s\n", iface.Name)
			if iface.State == net.LinkDown {
				s.Printf(" shutdown\n")
			}
			if iface.HasIP() {
				s.Printf(" ip address %s %s\n", iface.IP.String(), iface.Mask.String())
			}
			s.Printf("!\n")
		}
		for _, r := range s.Dev.Net.Routes.Sorted() {
			if r.Proto == "S" {
				s.Printf("ip route %s %s %s\n", r.Prefix.String(), r.Mask.String(), r.NextHop.String())
			}
		}
		s.Printf("!\n")
		return nil
	}
}

func staticRouteHandler(s *cli.Session) func(*cli.Session, []string) error {
	return func(s *cli.Session, args []string) error {
		prefix, err := net.ParseIP(args[0])
		if err != nil {
			s.Printf("  %s\n", "Invalid input detected")
			return nil
		}
		mask, err := net.ParseMask(args[1])
		if err != nil {
			s.Printf("  %s\n", "Invalid input detected")
			return nil
		}
		nh, err := net.ParseIP(args[2])
		if err != nil {
			s.Printf("  %s\n", "Invalid input detected")
			return nil
		}
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
			s.Printf("  %s\n", "Invalid input detected")
			return nil
		}
		mask, err := net.ParseMask(args[1])
		if err != nil {
			s.Printf("  %s\n", "Invalid input detected")
			return nil
		}
		iface := s.Dev.GetIface(name)
		if iface == nil {
			s.Printf("  %s\n", "Invalid input detected")
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