package main

import "os"

// flagServer and restArgs do minimal manual parsing of the one global
// flag (-s/--server) so it can appear before OR after the subcommand
// (`flyball -s humidity read x` and `flyball read x -s humidity` both
// work) -- stdlib flag.Parse alone only handles flags-before-positional,
// which doesn't fit a command whose own arguments (e.g. `demand ADDR
// VALUE`) shouldn't be flag-parsed themselves.
func flagServer() string {
	args := os.Args[1:]
	for i, a := range args {
		if (a == "-s" || a == "--server") && i+1 < len(args) {
			return args[i+1]
		}
	}
	return ""
}

func restArgs() []string {
	args := os.Args[1:]
	out := make([]string, 0, len(args))
	for i := 0; i < len(args); i++ {
		if (args[i] == "-s" || args[i] == "--server") && i+1 < len(args) {
			i++
			continue
		}
		out = append(out, args[i])
	}
	return out
}

// flag helper: true if name (bool flag, e.g. --fresh) is present anywhere
// in args; returns the remaining args with it removed.
func popBool(args []string, name string) (bool, []string) {
	out := make([]string, 0, len(args))
	found := false
	for _, a := range args {
		if a == name {
			found = true
			continue
		}
		out = append(out, a)
	}
	return found, out
}

// popValue finds --name VALUE anywhere in args, returning the value and
// the remaining args with both removed.
func popValue(args []string, name string) (string, []string, bool) {
	out := make([]string, 0, len(args))
	value := ""
	found := false
	for i := 0; i < len(args); i++ {
		if args[i] == name && i+1 < len(args) {
			value = args[i+1]
			found = true
			i++
			continue
		}
		out = append(out, args[i])
	}
	return value, out, found
}
