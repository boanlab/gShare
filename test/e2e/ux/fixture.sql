-- Synthetic data plane for the UX audit: a connected cluster with four nodes and eleven GPUs,
-- covering exclusive and fractional modes, a cordoned node, a degraded device and a lent-out card.
--
-- The control plane cannot tell this apart from inventory an operator reported, so every console
-- screen that reads nodes, devices or capacity renders as it would against real hardware. Nothing
-- here can run CUDA — sessions built on it stay in pending, which is itself a state worth auditing.
--
--   docker compose exec -T postgres psql -U gshare -d gshare < test/e2e/ux/fixture.sql

BEGIN;

INSERT INTO cluster (id, name, role, api_server, runtime, status, kubeconfig_secret_ref, created_at, updated_at)
VALUES ('clu_ux0000000000000000000001', 'lab-seoul', 'primary', 'https://10.0.0.10:6443', 'containerd', 'connected', 'ux-fixture', now() - interval '38 days', now())
ON CONFLICT (id) DO UPDATE SET status = 'connected';

INSERT INTO gpu_node (id, cluster_id, hostname, status, cpu, mem, disk, region, lossless_capable, created_at, updated_at) VALUES
  ('nod_ux0000000000000000000001', 'clu_ux0000000000000000000001', 'gpu-01', 'ready',       64, 512, 3600, 'seoul-a', true,  now() - interval '38 days', now()),
  ('nod_ux0000000000000000000002', 'clu_ux0000000000000000000001', 'gpu-02', 'ready',       64, 512, 3600, 'seoul-a', true,  now() - interval '38 days', now()),
  ('nod_ux0000000000000000000003', 'clu_ux0000000000000000000001', 'gpu-03', 'cordoned',    32, 256, 1800, 'seoul-b', false, now() - interval '12 days', now()),
  ('nod_ux0000000000000000000004', 'clu_ux0000000000000000000001', 'cpu-01', 'ready',       32, 128,  900, 'seoul-a', false, now() - interval '38 days', now())
ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status;

INSERT INTO gpu_device (id, node_id, cluster_id, model, gpu_uuid, total_mem_mb, used_mem_mb, total_cores, used_cores, status, mode, lend_state, created_at, updated_at) VALUES
  -- gpu-01: fractional, partly consumed
  ('dev_ux0000000000000000000001', 'nod_ux0000000000000000000001', 'clu_ux0000000000000000000001', 'NVIDIA RTX PRO 6000', 'GPU-ux00-0001', 98304, 49152, 100, 50, 'ready',    'fractional', '',        now() - interval '38 days', now()),
  ('dev_ux0000000000000000000002', 'nod_ux0000000000000000000001', 'clu_ux0000000000000000000001', 'NVIDIA RTX PRO 6000', 'GPU-ux00-0002', 98304, 12288, 100, 20, 'ready',    'fractional', '',        now() - interval '38 days', now()),
  ('dev_ux0000000000000000000003', 'nod_ux0000000000000000000001', 'clu_ux0000000000000000000001', 'NVIDIA RTX PRO 6000', 'GPU-ux00-0003', 98304,     0, 100,  0, 'ready',    'fractional', 'yielded', now() - interval '38 days', now()),
  ('dev_ux0000000000000000000004', 'nod_ux0000000000000000000001', 'clu_ux0000000000000000000001', 'NVIDIA RTX PRO 6000', 'GPU-ux00-0004', 98304, 98304, 100,100, 'ready',    'fractional', '',        now() - interval '38 days', now()),
  -- gpu-02: exclusive cards
  ('dev_ux0000000000000000000005', 'nod_ux0000000000000000000002', 'clu_ux0000000000000000000001', 'NVIDIA H100 80GB',    'GPU-ux00-0005', 81920,     0, 100,  0, 'ready',    'exclusive',  '',        now() - interval '38 days', now()),
  ('dev_ux0000000000000000000006', 'nod_ux0000000000000000000002', 'clu_ux0000000000000000000001', 'NVIDIA H100 80GB',    'GPU-ux00-0006', 81920, 81920, 100,100, 'ready',    'exclusive',  '',        now() - interval '38 days', now()),
  ('dev_ux0000000000000000000007', 'nod_ux0000000000000000000002', 'clu_ux0000000000000000000001', 'NVIDIA H100 80GB',    'GPU-ux00-0007', 81920,     0, 100,  0, 'ready',    'exclusive',  'lent',    now() - interval '38 days', now()),
  ('dev_ux0000000000000000000008', 'nod_ux0000000000000000000002', 'clu_ux0000000000000000000001', 'NVIDIA H100 80GB',    'GPU-ux00-0008', 81920,     0, 100,  0, 'degraded', 'exclusive',  '',        now() - interval '38 days', now()),
  -- gpu-03: cordoned node, cards still inventoried
  ('dev_ux0000000000000000000009', 'nod_ux0000000000000000000003', 'clu_ux0000000000000000000001', 'NVIDIA RTX 4090',     'GPU-ux00-0009', 24576,     0, 100,  0, 'ready',    'fractional', '',        now() - interval '12 days', now()),
  ('dev_ux0000000000000000000010', 'nod_ux0000000000000000000003', 'clu_ux0000000000000000000001', 'NVIDIA RTX 4090',     'GPU-ux00-0010', 24576,  6144, 100, 25, 'ready',    'fractional', '',        now() - interval '12 days', now()),
  ('dev_ux0000000000000000000011', 'nod_ux0000000000000000000003', 'clu_ux0000000000000000000001', 'NVIDIA RTX 4090',     'GPU-ux00-0011', 24576,     0, 100,  0, 'offline',  'fractional', '',        now() - interval '12 days', now())
ON CONFLICT (id) DO UPDATE SET used_mem_mb = EXCLUDED.used_mem_mb, used_cores = EXCLUDED.used_cores, status = EXCLUDED.status, lend_state = EXCLUDED.lend_state;

COMMIT;

-- ── Sessions across every lifecycle state ────────────────────────────────────────────────────
-- Written directly because creating them through the API needs a reachable Kubernetes API server.
-- Owners, offerings, images and wallets are resolved from whatever the demo seed created, so this
-- block is safe to re-run and adapts to any seeded organization.
BEGIN;

INSERT INTO session (
  id, owner_user_id, group_id, cluster_id, cluster_mode, offering_id, image_id, resource_class,
  mode, gpu_mem_mb, gpu_cores, bound_gpu_uuid, billing_wallet_id, status, credit_per_hour_snapshot,
  device_total_mem_mb, pod_ref, name, cpu, mem_gb, disk_gb, lossless_pause, pause_mode,
  preemptible, priority, started_at, terminated_at, created_at, updated_at)
SELECT
  s.id, u.id, m.group_id, 'clu_ux0000000000000000000001', 'single', o.id, i.id, 'gpu',
  s.mode, s.gpu_mem_mb, s.gpu_cores, s.gpu_uuid, w.id, s.status, s.rate,
  s.dev_mem, 'gshare-sessions/' || s.id, s.name, s.cpu, s.mem_gb, s.disk_gb, s.lossless, s.pause_mode,
  s.preemptible, s.priority, s.started_at, s.terminated_at, s.created_at, now()
FROM (VALUES
  ('ses_ux0000000000000000000001', 'vit-base-ft',      'fractional', 24576,  25, 'GPU-ux00-0001', 'running',    4.50, 98304, 8,  64, 100, true,  'yield', false, 0, now() - interval '6 hours',  NULL,                        now() - interval '6 hours'),
  ('ses_ux0000000000000000000002', 'llama3-eval',      'exclusive',  81920, 100, 'GPU-ux00-0006', 'running',   18.00, 81920, 16, 128, 200, false, 'cold', false, 5, now() - interval '2 days',    NULL,                        now() - interval '2 days'),
  ('ses_ux0000000000000000000003', 'notebook-scratch', 'fractional', 12288,  20, 'GPU-ux00-0002', 'running',    2.25, 98304, 4,  32,  50, false, 'cold', true,  0, now() - interval '25 minutes', NULL,                       now() - interval '25 minutes'),
  ('ses_ux0000000000000000000004', 'sweep-lr-0003',    'fractional', 24576,  25, NULL,            'pending',    4.50, NULL,  8,  64, 100, false, 'cold', true,  0, NULL,                          NULL,                       now() - interval '4 minutes'),
  ('ses_ux0000000000000000000005', 'diffusion-train',  'fractional', 49152,  50, 'GPU-ux00-0003', 'paused',     9.00, 98304, 8,  64, 100, true,  'yield', false, 0, now() - interval '3 days',    NULL,                        now() - interval '3 days'),
  ('ses_ux0000000000000000000006', 'bert-baseline',    'fractional', 24576,  25, NULL,            'terminated', 4.50, 98304, 8,  64, 100, false, 'cold', false, 0, now() - interval '9 days',    now() - interval '8 days',   now() - interval '9 days'),
  ('ses_ux0000000000000000000007', 'ocr-pipeline',     'exclusive',  81920, 100, NULL,            'terminated',18.00, 81920, 16, 128, 200, false, 'cold', false, 0, now() - interval '30 days',   now() - interval '29 days',  now() - interval '30 days'),
  ('ses_ux0000000000000000000008', 'stale-loader',     'fractional', 12288,  20, NULL,            'error',      2.25, NULL,  4,  32,  50, false, 'cold', false, 0, NULL,                          now() - interval '11 hours', now() - interval '12 hours')
) AS s(id, name, mode, gpu_mem_mb, gpu_cores, gpu_uuid, status, rate, dev_mem, cpu, mem_gb, disk_gb, lossless, pause_mode, preemptible, priority, started_at, terminated_at, created_at)
CROSS JOIN LATERAL (SELECT id FROM "user" WHERE email = 'haneul@nexusai.dev' LIMIT 1) u
CROSS JOIN LATERAL (SELECT group_id FROM membership WHERE user_id = u.id AND group_id IS NOT NULL LIMIT 1) m
CROSS JOIN LATERAL (SELECT id FROM offering WHERE status = 'active' ORDER BY created_at LIMIT 1) o
CROSS JOIN LATERAL (SELECT id FROM image ORDER BY created_at LIMIT 1) i
CROSS JOIN LATERAL (SELECT id FROM credit_wallet WHERE owner_type = 'user' AND owner_id = u.id LIMIT 1) w
ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status, updated_at = now();

COMMIT;

-- ── Restore demo accounts ─────────────────────────────────────────────────────────────────────
-- The audit drives real screens, and earlier revisions of the delete confirmation let a probe
-- complete a soft delete. The delete path now requires the account's address to be typed, so this
-- is a safety net rather than a routine repair: it makes a run repeatable whatever the previous
-- one left behind.
UPDATE "user"
   SET deleted_at = NULL, status = 'active'
 WHERE deleted_at IS NOT NULL
   AND email LIKE '%@nexusai.dev';
