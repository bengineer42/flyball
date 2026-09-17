// `flyball login`/`logout`: trade a runner's password (or its bearer
// token) for a session cookie via POST /api/auth/login, and persist it
// so later invocations reuse it -- see internal/client/auth.go for the
// storage and internal/client/resolve.go for where the saved cookie is
// picked back up (Target.AuthHeaders). Mirrors what the UI's login page
// does (POST the secret, get a cookie); the old Python cli.py never had
// this -- only a bearer token, taken via --token/FLYBALL_TOKEN.
package main

import (
	"fmt"
	"os"

	"flyballd/internal/client"
	"golang.org/x/term"
)

func runLoginCommand(t client.Target, args []string) error {
	var secret string
	switch len(args) {
	case 0:
		fmt.Fprint(os.Stderr, "Password: ")
		data, err := term.ReadPassword(int(os.Stdin.Fd()))
		fmt.Fprintln(os.Stderr)
		if err != nil {
			return fmt.Errorf("reading password: %w", err)
		}
		secret = string(data)
	case 1:
		secret = args[0]
	default:
		return fmt.Errorf("usage: flyball login [SECRET]")
	}
	if secret == "" {
		return fmt.Errorf("an empty password is no password")
	}
	if err := client.Login(t, secret); err != nil {
		return err
	}
	fmt.Println("signed in")
	return nil
}

func runLogoutCommand(t client.Target) error {
	if err := client.Logout(t); err != nil {
		return err
	}
	fmt.Println("signed out")
	return nil
}
