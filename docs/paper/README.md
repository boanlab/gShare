# GShare — paper and reproducibility artifact

> 📚 [Documentation home](../README.md)

This directory holds the GShare manuscript and the artifact needed to reproduce its
results.

- **[`manuscript/`](manuscript/)** — the paper itself (English, Elsevier `elsarticle`,
  targeting FGCS). Self-contained: `main.tex`, `refs.bib`, and `figures/`, plus
  `highlights.txt`, `cover-letter.txt`, and `declarations.txt`. Upload the whole
  `manuscript/` directory to Overleaf and compile.
- **[`eval/`](eval/)** — the reproducibility artifact: the measurement scripts, raw data
  (CSV and logs), figure-generation code, and procedure notes behind every figure and
  number in the paper. Start from [`eval/README.md`](eval/README.md), which maps each
  figure to the script that produced it. The primary measurement records are
  `eval/e2e/mt_e2e.md` (two-node multi-tenant), `eval/e2e/pageability.md` (T2 swap and
  pageability), `eval/o1/reclaim_e2e.md` (reclaim decomposition), `eval/e2e/mps_sota.md`
  (real MPS), and `eval/sim/trace_impact.md` (the Alibaba trace).

## Manuscript structure

The paper follows the usual systems-paper arc: Introduction → Background and Motivation →
Related Work → Design (the T0–T3 occupancy memory hierarchy, in-place yield, and the
economic and paging policies) → Implementation → Evaluation → Discussion and Limitations →
Conclusion.

> Earlier Korean design and evaluation notes — introduction, motivation, related work,
> design, evaluation, the approach document, and the evaluation plan — were distilled into
> `manuscript/main.tex` and removed. They remain recoverable from the git history at
> `docs/paper/*.md`.

The partitioning mechanism itself — fractional vGPUs and exclusive full cards — is
delegated to HAMi and the device plugin. GShare builds on top of it, treating occupancy as
a position in a memory hierarchy and managing placement, billing, and lossless
reclamation.
