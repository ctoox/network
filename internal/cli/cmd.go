package cli

// Cmd is a command node in the tree. Each node can have subcommands
// (children) or a Handler to execute.
type Cmd struct {
	// Name is the canonical (full) keyword.
	Name string
	// Aliases are unique prefixes allowed for completion, e.g. "sh" for "show".
	Aliases []string
	// Help text shown on `?` or `help`.
	Help string
	// Children holds sub-commands.
	Children []*Cmd
	// Handler executes the command. It receives the session and the
	// remaining args.
	Handler func(s *Session, args []string) error
	// MinArgs / MaxArgs validation before handler (optional).
	MinArgs int
	MaxArgs int
}

func (c *Cmd) Add(child *Cmd) *Cmd {
	c.Children = append(c.Children, child)
	return c
}

func (c *Cmd) Find(token string) *Cmd {
	// exact match first
	for _, ch := range c.Children {
		if ch.Name == token {
			return ch
		}
	}
	// prefix match
	var match *Cmd
	ambiguous := false
	for _, ch := range c.Children {
		if len(token) >= 1 && matchPrefix(token, ch.Name) {
			if match != nil {
				ambiguous = true
				continue
			}
			match = ch
		}
	}
	if ambiguous {
		return nil
	}
	return match
}

func matchPrefix(token, full string) bool {
	if len(token) > len(full) {
		return false
	}
	for i := 0; i < len(token); i++ {
		if token[i] != full[i] {
			return false
		}
	}
	return true
}

// TabExpand returns the expansion for a token, or "" if none.
func (c *Cmd) TabExpand(token string) string {
	var matches []string
	for _, ch := range c.Children {
		if matchPrefix(token, ch.Name) {
			matches = append(matches, ch.Name)
		}
	}
	if len(matches) == 1 {
		return matches[0]
	}
	return ""
}

// HelpText lists all available sub-commands.
func (c *Cmd) HelpText() []string {
	var out []string
	for _, ch := range c.Children {
		if ch.Help != "" {
			out = append(out, "  "+ch.Name+"  "+ch.Help)
		} else {
			out = append(out, "  "+ch.Name)
		}
	}
	return out
}