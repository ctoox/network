package cli

import (
	"fmt"
	"strings"

	"netsim/internal/net"
)

// View is a CLI state: a prompt plus the command tree available in it.
type View struct {
	// ID is used to identify the view type (e.g. "user", "system", "interface").
	ID string
	// Prompt renders the prompt string for the current device.
	Prompt func(s *Session) string
	// Tree is the command root for this view.
	Tree *Cmd
	// OnEnter / OnLeave hooks.
	OnEnter func(s *Session) error
	OnLeave func(s *Session) error
}

// Session is one active CLI connection to a device.
type Session struct {
	// DeviceID refers to the simulated device.
	DeviceID string
	// DeviceName is the current sysname/hostname.
	DeviceName string
	// Output collects the output of the last command.
	Output strings.Builder
	// views is the stack of views.
	views []*View
	// Vendor is the vendor-specific implementation.
	Vendor Vendor
	// Dev is the simulated device (set by the server when creating session).
	Dev *net.Device
	// World is the simulation world (set by server).
	World *net.World
	// Lines on startup banner.
	Lines []string
}

func (s *Session) Push(view *View) {
	if view.OnEnter != nil {
		view.OnEnter(s)
	}
	s.views = append(s.views, view)
}

func (s *Session) Pop() *View {
	if len(s.views) <= 1 {
		return s.views[0]
	}
	top := s.views[len(s.views)-1]
	if top.OnLeave != nil {
		top.OnLeave(s)
	}
	s.views = s.views[:len(s.views)-1]
	return s.Current()
}

func (s *Session) Return() *View {
	if len(s.views) <= 1 {
		return s.views[0]
	}
	top := s.views[len(s.views)-1]
	if top.OnLeave != nil {
		top.OnLeave(s)
	}
	s.views = s.views[:1]
	return s.views[0]
}

func (s *Session) Current() *View {
	return s.views[len(s.views)-1]
}

// TabExpand completes the last token of a line against the current tree.
func (s *Session) TabExpand(line string) string {
	fields := tokenize(line)
	if len(fields) == 0 {
		return line
	}
	tree := s.Current().Tree
	// walk down through resolved prefixes, but keep the last token for expansion
	i := 0
	for ; i < len(fields)-1; i++ {
		node := tree.Find(fields[i])
		if node == nil || len(node.Children) == 0 {
			break
		}
		tree = node
	}
	last := fields[len(fields)-1]
	exp := tree.TabExpand(last)
	if exp == "" {
		return line
	}
	fields[len(fields)-1] = exp
	return strings.Join(fields, " ")
}

func (s *Session) Prompt() string {
	if len(s.views) == 0 {
		return ""
	}
	return s.Current().Prompt(s)
}

func (s *Session) Printf(format string, args ...interface{}) {
	fmt.Fprintf(&s.Output, format, args...)
}

func (s *Session) Println(args ...interface{}) {
	fmt.Fprintln(&s.Output, args...)
}

func (s *Session) Print(str string) {
	s.Output.WriteString(str)
}

// Execute parses and runs a command line in the current view.
func (s *Session) Execute(line string) (string, error) {
	s.Output.Reset()
	fields := tokenize(line)
	if len(fields) == 0 {
		return "", nil
	}
	tree := s.Current().Tree
	if fields[0] == "?" {
		for _, l := range tree.HelpText() {
			s.Println(l)
		}
		return s.Output.String(), nil
	}
	return s.run(tree, fields)
}

// RunAs executes a line against a temporary tree (used by `do` commands)
// without changing the current view. Output is appended to the session.
func (s *Session) RunAs(tree *Cmd, line string) {
	fields := tokenize(line)
	if len(fields) == 0 {
		return
	}
	s.run(tree, fields)
}

func (s *Session) run(tree *Cmd, args []string) (string, error) {
	first := args[0]
	if first == "?" {
		for _, l := range tree.HelpText() {
			s.Println(l)
		}
		return s.Output.String(), nil
	}
	node := tree.Find(first)
	if node == nil {
		s.Printf("  Unknown command, or the command does not have the specified parameters.\n")
		return s.Output.String(), nil
	}
	if len(node.Children) > 0 {
		// navigate into the subtree
		return s.run(node, args[1:])
	}
	if node.Handler == nil {
		s.Printf("  Incomplete command.\n")
		return s.Output.String(), nil
	}
	if len(args)-1 < node.MinArgs {
		s.Printf("  Incomplete command.\n")
		return s.Output.String(), nil
	}
	if err := node.Handler(s, args[1:]); err != nil {
		s.Printf("  %s\n", err.Error())
	}
	return s.Output.String(), nil
}

func tokenize(line string) []string {
	return strings.Fields(line)
}

// Vendor abstracts a device operating system's CLI.
type Vendor interface {
	// Name returns e.g. "VRP" or "IOS".
	Name() string
	// NewSession builds a session rooted at the top-level view.
	NewSession(deviceID, deviceName string) *Session
	// VersionInfo returns the display version banner.
	VersionInfo() string
}