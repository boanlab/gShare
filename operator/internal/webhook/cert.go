/*
Package webhook implements the GShare pod lend-guard ValidatingWebhook.

It elevates the borrow lend-invariant from an operator self-check to apiserver
admission: any Pod that pins a GPU via the device-plugin BYPASS
(NVIDIA_VISIBLE_DEVICES=<uuid>, no nvidia.com/gpu request) is admitted only if the
card is held by a yielded/lent resident session and not already lent — at most one
bypass Pod per card. The backend ledger remains the primary, atomic enforcement;
this is cluster-side defense-in-depth (failurePolicy Ignore).

Serving TLS is self-managed: the operator generates a self-signed cert for the
webhook Service DNS at startup, writes it to the controller-runtime cert dir, and
patches the ValidatingWebhookConfiguration caBundle — no cert-manager dependency.
*/
package webhook

import (
	"crypto/rand"
	"crypto/rsa"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"fmt"
	"math/big"
	"os"
	"path/filepath"
	"time"
)

// GenerateSelfSignedCert writes tls.crt/tls.key (valid for dnsNames) into dir and
// returns the PEM cert to use as the webhook caBundle. The cert is self-signed and
// marked as a CA so it verifies as its own chain root.
func GenerateSelfSignedCert(dir string, dnsNames []string, notAfter time.Time) (caPEM []byte, err error) {
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		return nil, fmt.Errorf("generate key: %w", err)
	}
	tmpl := &x509.Certificate{
		SerialNumber:          big.NewInt(1),
		Subject:               pkix.Name{CommonName: "gshare-webhook-ca"},
		DNSNames:              dnsNames,
		NotBefore:             time.Now().Add(-time.Hour),
		NotAfter:              notAfter,
		KeyUsage:              x509.KeyUsageDigitalSignature | x509.KeyUsageCertSign,
		ExtKeyUsage:           []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
		BasicConstraintsValid: true,
		IsCA:                  true,
	}
	der, err := x509.CreateCertificate(rand.Reader, tmpl, tmpl, &key.PublicKey, key)
	if err != nil {
		return nil, fmt.Errorf("create cert: %w", err)
	}
	certPEM := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})
	keyPEM := pem.EncodeToMemory(&pem.Block{Type: "RSA PRIVATE KEY", Bytes: x509.MarshalPKCS1PrivateKey(key)})

	if err := os.MkdirAll(dir, 0o755); err != nil {
		return nil, fmt.Errorf("mkdir cert dir: %w", err)
	}
	if err := os.WriteFile(filepath.Join(dir, "tls.crt"), certPEM, 0o600); err != nil {
		return nil, fmt.Errorf("write tls.crt: %w", err)
	}
	if err := os.WriteFile(filepath.Join(dir, "tls.key"), keyPEM, 0o600); err != nil {
		return nil, fmt.Errorf("write tls.key: %w", err)
	}
	return certPEM, nil
}

// ServiceDNSNames returns the in-cluster DNS SANs for a Service.
func ServiceDNSNames(service, namespace string) []string {
	base := service + "." + namespace + ".svc"
	return []string{base, base + ".cluster.local"}
}
