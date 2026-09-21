package main

import "testing"

// TestVerifyAgainstPython checks a hash produced by Python's
// hash_password (flyball.interfaces.server.auth) against the Go verifyPassword,
// proving the format/parsing round-trips in the Python -> Go direction.
func TestVerifyAgainstPython(t *testing.T) {
	const pythonHash = "$scrypt$n=16384,r=8,p=1$36M9tNDQnT4LXnO_XFWyaA$H_N0KkLlVTY4PF16x5xY16V42DHL6cAnJKuvBxBjFRjuHTForWqlYIRGmlH0_9ehZ_2RmUJWhS_k8bCiO0V6eg"
	if !verifyPassword("anotherpass", pythonHash) {
		t.Fatal("correct password did not verify against Python-produced hash")
	}
	if verifyPassword("wrongpass", pythonHash) {
		t.Fatal("wrong password verified against Python-produced hash")
	}
}

func TestHashPasswordRoundTrip(t *testing.T) {
	hash, err := hashPassword("roundtrip")
	if err != nil {
		t.Fatal(err)
	}
	if !verifyPassword("roundtrip", hash) {
		t.Fatal("Go hash did not self-verify")
	}
}

func TestIdentifier(t *testing.T) {
	cases := map[string]string{
		"chiller":   "chiller",
		"lab-probe": "lab_probe",
		"My Device": "my_device",
	}
	for in, want := range cases {
		got, err := identifier(in)
		if err != nil {
			t.Fatalf("%q: %v", in, err)
		}
		if got != want {
			t.Fatalf("%q: got %q, want %q", in, got, want)
		}
	}
	for _, bad := range []string{"class", "1abc", "___"} {
		if _, err := identifier(bad); err == nil {
			t.Fatalf("%q: expected error", bad)
		}
	}
}
