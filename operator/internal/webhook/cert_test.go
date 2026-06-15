package webhook

import (
	"crypto/x509"
	"encoding/pem"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// The serving cert must be valid NOW (a future NotBefore makes the apiserver reject it
// with "tls: bad certificate" and the webhook silently fails open).
func TestGeneratedCertValidNow(t *testing.T) {
	dir := t.TempDir()
	caPEM, err := GenerateSelfSignedCert(dir, ServiceDNSNames("gshare-webhook", "gshare-system"), time.Now().AddDate(1, 0, 0))
	if err != nil {
		t.Fatal(err)
	}
	block, _ := pem.Decode(caPEM)
	if block == nil {
		t.Fatal("caPEM is not PEM")
	}
	cert, err := x509.ParseCertificate(block.Bytes)
	if err != nil {
		t.Fatal(err)
	}
	now := time.Now()
	if now.Before(cert.NotBefore) || now.After(cert.NotAfter) {
		t.Fatalf("cert not valid now: NotBefore=%s NotAfter=%s now=%s", cert.NotBefore, cert.NotAfter, now)
	}
	if err := cert.VerifyHostname("gshare-webhook.gshare-system.svc"); err != nil {
		t.Fatalf("SAN mismatch: %v", err)
	}
	if _, err := os.Stat(filepath.Join(dir, "tls.key")); err != nil {
		t.Fatalf("tls.key not written: %v", err)
	}
}
