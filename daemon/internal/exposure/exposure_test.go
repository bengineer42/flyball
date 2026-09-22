package exposure

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestIsLoopback(t *testing.T) {
	for addr, want := range map[string]bool{
		"127.0.0.1:8000": true, "localhost:8000": true, "[::1]:8000": true, "127.0.1.1:80": true,
		"127.0.0.1": true, "::1": true, "LOCALHOST": true,
		":8000": false, "0.0.0.0:8000": false, "[::]:8000": false, "192.168.1.3:8000": false,
		"pi.local:8000": false, "": false,
	} {
		if got := IsLoopback(addr); got != want {
			t.Errorf("IsLoopback(%q) = %v, want %v", addr, got, want)
		}
	}
}

func TestDecide(t *testing.T) {
	open, token := Door{}, Door{Token: true}
	if w, err := Decide("127.0.0.1:8000", open, false); w != "" || err != nil {
		t.Errorf("loopback, open: (%q, %v), want nothing", w, err)
	}
	if _, err := Decide(":8000", open, false); err == nil || !strings.Contains(err.Error(), "every interface") {
		t.Errorf("every interface, open: %v, want a refusal naming it", err)
	}
	if w, err := Decide("0.0.0.0:8000", open, true); err != nil || !strings.Contains(w, "OPEN") {
		t.Errorf("opted in: (%q, %v), want the open warning", w, err)
	}
	if w, err := Decide("0.0.0.0:8000", token, false); err != nil || !strings.Contains(w, "unencrypted") {
		t.Errorf("with a token: (%q, %v), want the cleartext warning", w, err)
	}
	if w, err := Decide("0.0.0.0:8000", Door{Password: true}, false); err != nil || w == "" {
		t.Errorf("with a password: (%q, %v), want the cleartext warning", w, err)
	}
}

func TestProbe(t *testing.T) {
	for body, want := range map[string]Door{
		`{"password": false, "token": false, "level": "operate"}`: {},
		`{"password": true, "token": false}`:                      {Password: true},
		`{"password": false, "token": true}`:                      {Token: true},
	} {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.Write([]byte(body))
		}))
		got, err := Probe(context.Background(), srv.Client(), srv.URL+"/api/auth")
		srv.Close()
		if err != nil || got != want {
			t.Errorf("Probe(%s) = (%+v, %v), want %+v", body, got, err, want)
		}
	}
	for name, h := range map[string]http.HandlerFunc{
		"404":         func(w http.ResponseWriter, r *http.Request) { http.NotFound(w, r) },
		"not json":    func(w http.ResponseWriter, r *http.Request) { w.Write([]byte("<html>")) },
		"fields gone": func(w http.ResponseWriter, r *http.Request) { w.Write([]byte(`{"level":"read"}`)) },
		"one field":   func(w http.ResponseWriter, r *http.Request) { w.Write([]byte(`{"password":true}`)) },
	} {
		srv := httptest.NewServer(h)
		_, err := Probe(context.Background(), srv.Client(), srv.URL+"/api/auth")
		srv.Close()
		if !errors.Is(err, ErrNotADoor) {
			t.Errorf("%s: err = %v, want ErrNotADoor", name, err)
		}
	}
	_, err := Probe(context.Background(), http.DefaultClient, "http://127.0.0.1:1/api/auth")
	if err == nil || errors.Is(err, ErrNotADoor) {
		t.Errorf("nothing listening: err = %v, want a connection error, not ErrNotADoor", err)
	}
}
