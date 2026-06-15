// E2E workload — cuBLAS SGEMM loop with controllable VRAM footprint, compute volume,
// and injected idle gaps. One workload serves every E-series condition (resident with
// idle bursts, continuous spot, Solo, Orion client). Real FLOPs → reports achieved
// GFLOP/s and per-iteration progress; emits unix timestamps so an external orchestrator
// can measure resume latency and build occupancy timelines.
//
// Build: nvcc -O3 gpu_sgemm.cu -lcublas -o gpu_sgemm   (inside nvidia/cuda:*-devel)
// Run:   ./gpu_sgemm --mem-gb 6 --secs 120 --idle-every 20 --idle-sec 5 --tag R
//
// Flags:
//   --mem-gb F     target VRAM footprint (sizes the square matrices); default 4
//   --secs F       total wall-clock budget (compute + idle); default 60
//   --idle-every N inject an idle gap after every N SGEMM iterations (0 = never)
//   --idle-sec F   idle gap length in seconds; default 0
//   --tag S        label prefix on every emitted line (R / S / solo / ...)
//   --ckpt-marker  print "CKPT t=..." instead of sleeping at each idle point
//                  (lets an app-ckpt orchestrator hook checkpoint/restore between bursts)
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <unistd.h>
#include <cuda_runtime.h>
#include <cublas_v2.h>

static double now_s() {
  struct timespec ts; clock_gettime(CLOCK_REALTIME, &ts);
  return ts.tv_sec + ts.tv_nsec * 1e-9;
}
#define CK(x) do { cudaError_t e=(x); if(e!=cudaSuccess){fprintf(stderr,"cuda err %s:%d %s\n",__FILE__,__LINE__,cudaGetErrorString(e));exit(2);} } while(0)

int main(int argc, char** argv) {
  double mem_gb = 4, secs = 60, idle_sec = 0;
  long idle_every = 0;
  const char* tag = "W";
  bool ckpt_marker = false;
  for (int i = 1; i < argc; i++) {
    if (!strcmp(argv[i], "--mem-gb")) mem_gb = atof(argv[++i]);
    else if (!strcmp(argv[i], "--secs")) secs = atof(argv[++i]);
    else if (!strcmp(argv[i], "--idle-every")) idle_every = atol(argv[++i]);
    else if (!strcmp(argv[i], "--idle-sec")) idle_sec = atof(argv[++i]);
    else if (!strcmp(argv[i], "--tag")) tag = argv[++i];
    else if (!strcmp(argv[i], "--ckpt-marker")) ckpt_marker = true;
  }
  // 3 square matrices of N×N float32 ≈ mem_gb → N = sqrt(mem_gb*1e9 / 12)
  long N = (long)(sqrt(mem_gb * 1e9 / 12.0));
  N = (N / 256) * 256; if (N < 256) N = 256;
  size_t bytes = (size_t)N * N * sizeof(float);
  float *dA, *dB, *dC;
  CK(cudaMalloc(&dA, bytes)); CK(cudaMalloc(&dB, bytes)); CK(cudaMalloc(&dC, bytes));
  CK(cudaMemset(dA, 1, bytes)); CK(cudaMemset(dB, 1, bytes)); CK(cudaMemset(dC, 0, bytes));
  cublasHandle_t h; cublasCreate(&h);
  const float alpha = 1.f, beta = 0.f;
  double flop_per_iter = 2.0 * (double)N * N * N;   // SGEMM N^3 MACs

  size_t freeb, totalb; CK(cudaMemGetInfo(&freeb, &totalb));
  printf("%s READY pid=%d N=%ld vram_used_mib=%.0f t=%.3f\n",
         tag, getpid(), N, (totalb - freeb) / 1048576.0, now_s());
  fflush(stdout);

  double t0 = now_s(), last = t0;
  long it = 0, done_iters_at_last_report = 0;
  while (now_s() - t0 < secs) {
    cublasSgemm(h, CUBLAS_OP_N, CUBLAS_OP_N, N, N, N, &alpha, dA, N, dB, N, &beta, dC, N);
    CK(cudaDeviceSynchronize());
    it++;
    double t = now_s();
    if (t - last >= 5.0) {   // periodic progress/throughput line
      double gflops = (flop_per_iter * (it - done_iters_at_last_report)) / (t - last) / 1e9;
      printf("%s PROG iter=%ld gflops=%.1f t=%.3f\n", tag, it, gflops, t);
      fflush(stdout);
      last = t; done_iters_at_last_report = it;
    }
    if (idle_every > 0 && it % idle_every == 0) {
      if (ckpt_marker) { printf("%s CKPT iter=%ld t=%.3f\n", tag, it, now_s()); fflush(stdout); }
      else if (idle_sec > 0) { printf("%s IDLE iter=%ld sec=%.1f t=%.3f\n", tag, it, idle_sec, now_s()); fflush(stdout); usleep((useconds_t)(idle_sec * 1e6)); }
    }
  }
  double tot = now_s() - t0;
  double avg_gflops = (flop_per_iter * it) / tot / 1e9;
  printf("%s DONE iters=%ld wall=%.2f avg_gflops=%.1f tflops=%.2f t=%.3f\n",
         tag, it, tot, avg_gflops, avg_gflops / 1000.0, now_s());
  cublasDestroy(h); cudaFree(dA); cudaFree(dB); cudaFree(dC);
  return 0;
}
