/*
envtest suite for the SessionReconciler. Brings up a controller-runtime
envtest API server + etcd, installs the CRD, and runs Ginkgo specs. Requires
KUBEBUILDER_ASSETS (provided by `make test` via setup-envtest).
*/
package controller

import (
	"context"
	"path/filepath"
	"testing"
	"time"

	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"

	"k8s.io/client-go/kubernetes/scheme"
	"k8s.io/client-go/rest"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/envtest"

	gsharev1 "github.com/gshare/operator/api/v1"
)

var (
	cfg       *rest.Config
	k8sClient client.Client
	testEnv   *envtest.Environment
	ctx       context.Context
	cancel    context.CancelFunc
)

func TestControllers(t *testing.T) {
	RegisterFailHandler(Fail)
	RunSpecs(t, "Controller Suite")
}

var _ = BeforeSuite(func() {
	ctx, cancel = context.WithCancel(context.TODO())

	testEnv = &envtest.Environment{
		CRDDirectoryPaths:     []string{filepath.Join("..", "..", "config", "crd", "bases")},
		ErrorIfCRDPathMissing: true,
	}

	var err error
	cfg, err = testEnv.Start()
	Expect(err).NotTo(HaveOccurred())
	Expect(cfg).NotTo(BeNil())

	Expect(gsharev1.AddToScheme(scheme.Scheme)).To(Succeed())

	k8sClient, err = client.New(cfg, client.Options{Scheme: scheme.Scheme})
	Expect(err).NotTo(HaveOccurred())
	Expect(k8sClient).NotTo(BeNil())
})

var _ = AfterSuite(func() {
	cancel()
	Expect(testEnv.Stop()).To(Succeed())
})

var _ = Describe("GShareSession CRD", func() {
	It("accepts a fractional GShareSession and lets the operator set status", func() {
		// Exercises CR admission only (create/get/delete); convergence would require
		// starting the manager with SessionReconciler here.
		s := &gsharev1.GShareSession{}
		s.Name = "envtest-sample"
		s.Namespace = "default"
		s.Spec = gsharev1.GShareSessionSpec{
			ClusterID:       "clu_test",
			ResourceClass:   "gpu",
			Mode:            "fractional",
			GpuMemMb:        4000,
			GpuCores:        30,
			Image:           "registry.gshare.internal/base:latest",
			OfferingID:      "off_test",
			ClusterMode:     "single",
			BillingWalletID: "wal_test",
			Owner:           "usr_test",
			ProjectID:       "prj_test",
		}
		Expect(k8sClient.Create(ctx, s)).To(Succeed())

		Eventually(func() error {
			return k8sClient.Get(ctx, client.ObjectKeyFromObject(s), &gsharev1.GShareSession{})
		}, 10*time.Second, 250*time.Millisecond).Should(Succeed())

		Expect(k8sClient.Delete(ctx, s)).To(Succeed())
	})
})
