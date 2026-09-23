// `flyball login`/`logout`: trade a front's admin password for a named
// token via POST /api/auth/login then POST /api/auth/tokens, and persist
// the token -- see internal/client/auth.go for the storage and exchange,
// and internal/client/resolve.go for where the saved token is picked
// back up (Target.AuthHeaders). The password is never a command-line
// argument (merge requirement 6's "never in argv" applies here too, not
// only to the front-dir key): it is always read from the terminal.
//
// `--scope` (repeatable) asks for more than the default read-only token,
// under D-036's four safeguards -- see internal/client/auth.go's
// resolveLoginScopes for the bare-verb rewrite and the manage refusal,
// and the warning below for the rest.
package main

import (
	"fmt"
	"os"

	"flyballd/internal/client"
	"golang.org/x/term"
)

func runLoginCommand(server string, args []string) error {
	scopes, args := popAllValues(args, "--scope")
	if len(args) > 1 {
		return fmt.Errorf("usage: flyball login [URL] [--scope SCOPE]...")
	}
	var target client.Target
	if len(args) == 1 {
		// An explicit URL addresses the front directly, same shape as
		// FLYBALL_URL, without requiring -s/FLYBALLD_URL to be set up
		// first just to sign in.
		target = client.Target{BaseURL: args[0]}
	} else {
		t, err := resolveTarget(server)
		if err != nil {
			return err
		}
		target = t
	}

	fmt.Fprint(os.Stderr, "Password: ")
	data, err := term.ReadPassword(int(os.Stdin.Fd()))
	fmt.Fprintln(os.Stderr)
	if err != nil {
		return fmt.Errorf("reading password: %w", err)
	}
	password := string(data)
	if password == "" {
		return fmt.Errorf("an empty password is no password")
	}

	tok, err := client.Login(target, password, client.LoginOptions{Scopes: scopes})
	if err != nil {
		return err
	}
	if tok.Elevated {
		// Safeguard 1: anything above read, named so it can be found and
		// revoked (`flyball token revoke`) without hunting for it.
		fmt.Fprintf(os.Stderr,
			"warning: saved token %q at %s carries %v -- anything running as this user can use it until it expires or is revoked with `flyball token revoke`\n",
			tok.Name, tok.Path, tok.Scopes)
	}
	fmt.Printf("signed in; saved token %q (scopes %v, expires %s)\n",
		tok.Name, tok.Scopes, tok.Expires.Format("2006-01-02T15:04:05Z07:00"))
	return nil
}

func runLogoutCommand(server string) error {
	target, err := resolveTarget(server)
	if err != nil {
		return err
	}
	if err := client.Logout(target); err != nil {
		return err
	}
	fmt.Println("signed out")
	return nil
}
