// `flyball login`/`logout`: trade a front's admin password for a named
// token via POST /api/auth/login then POST /api/auth/tokens, and persist
// the token -- see internal/client/auth.go for the storage and exchange,
// and internal/client/resolve.go for where the saved token is picked
// back up (Target.AuthHeaders). The password is never a command-line
// argument (merge requirement 6's "never in argv" applies here too, not
// only to the front-dir key): it is always read from the terminal.
package main

import (
	"fmt"
	"os"

	"flyballd/internal/client"
	"golang.org/x/term"
)

func runLoginCommand(server string, args []string) error {
	if len(args) > 1 {
		return fmt.Errorf("usage: flyball login [URL]")
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

	tok, err := client.Login(target, password)
	if err != nil {
		return err
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
