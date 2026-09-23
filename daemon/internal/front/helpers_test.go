package front

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/base64"
	"encoding/pem"
	"fmt"
	"math/big"
	"net/http"
	"os"
	"testing"
	"time"

	"golang.org/x/crypto/scrypt"
)

// testPassword is the admin password testScrypt holds.
const testPassword = "correct horse"

// testScrypt is a `$scrypt$` line for testPassword, cheap to check (n=16).
var testScrypt = scryptLine(testPassword, 16, 1)

func scryptLine(password string, n, r int) string {
	salt := []byte("0123456789abcdef")
	sum, err := scrypt.Key([]byte(password), salt, n, r, 1, 64)
	if err != nil {
		panic(err)
	}
	enc := base64.RawURLEncoding.EncodeToString
	return fmt.Sprintf("$scrypt$n=%d,r=%d,p=1$%s$%s", n, r, enc(salt), enc(sum))
}

// writeCert writes a self-signed ECDSA certificate for localhost.
func writeCert(t *testing.T, certPath, keyPath string) {
	t.Helper()
	priv, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	tmpl := &x509.Certificate{
		SerialNumber: big.NewInt(1),
		Subject:      pkix.Name{CommonName: "front-test"},
		NotBefore:    time.Now().Add(-time.Hour),
		NotAfter:     time.Now().Add(time.Hour),
		KeyUsage:     x509.KeyUsageDigitalSignature,
		ExtKeyUsage:  []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
		DNSNames:     []string{"localhost"},
		IPAddresses:  nil,
	}
	der, err := x509.CreateCertificate(rand.Reader, tmpl, tmpl, &priv.PublicKey, priv)
	if err != nil {
		t.Fatal(err)
	}
	keyDER, err := x509.MarshalECPrivateKey(priv)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(certPath, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der}), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(keyPath, pem.EncodeToMemory(&pem.Block{Type: "EC PRIVATE KEY", Bytes: keyDER}), 0o600); err != nil {
		t.Fatal(err)
	}
}

// stubClient is a proxy provider that answers as told.
type stubClient struct {
	id      Identity
	outcome Outcome
	err     error
}

func (stubClient) Name() string { return "proxy" }

func (s stubClient) Authenticate(*http.Request) (Identity, Outcome, error) {
	return s.id, s.outcome, s.err
}
