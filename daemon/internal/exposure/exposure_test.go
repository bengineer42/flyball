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
	if p := Decide("127.0.0.1:8000", open, false); p.Warning != "" || p.Addr != "127.0.0.1:8000" || p.Restricted() || p.OpenNetwork() {
		t.Errorf("loopback, open: %+v, want it as asked, nothing said", p)
	}
	p := Decide(":8000", open, false)
	if p.Addr != "127.0.0.1:8000" || !p.Restricted() || p.OpenNetwork() || !strings.Contains(p.Warning, "every interface") || !strings.Contains(p.Warning, "--insecure-open") {
		t.Errorf("every interface, open: %+v, want loopback on the same port, and why", p)
	}
	if e := p.Exposure(); e["restricted"] != true || e["host"] != "127.0.0.1" || e["port"] != 8000 || e["requested"] != ":8000" {
		t.Errorf("exposure = %v", e)
	}
	if p := Decide("[::]:8000", open, false); p.Addr != "127.0.0.1:8000" {
		t.Errorf("::, open: %+v, want 127.0.0.1", p)
	}
	if p := Decide("0.0.0.0:8000", open, true); p.Addr != "0.0.0.0:8000" || !p.OpenNetwork() || !strings.Contains(p.Warning, "OPEN") {
		t.Errorf("opted in: %+v, want as asked, with the open warning", p)
	}
	if p := Decide("0.0.0.0:8000", token, false); p.Addr != "0.0.0.0:8000" || p.OpenNetwork() || !strings.Contains(p.Warning, "unencrypted") {
		t.Errorf("with a token: %+v, want the cleartext warning", p)
	}
	if p := Decide("0.0.0.0:8000", Door{Password: true}, false); p.Restricted() || p.Warning == "" {
		t.Errorf("with a password: %+v, want the cleartext warning", p)
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
