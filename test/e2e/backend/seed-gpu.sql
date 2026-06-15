-- Configuration seed for the GPU session end-to-end test: offerings and a wallet only. Idempotent
-- through ON CONFLICT.
--
-- Note that GpuNode and GpuDevice inventory is no longer seeded. The operator's InventoryReconciler
-- reports node device-plugin capacity through POST /internal/inventory/gpu-devices, creating the
-- devices from a real node's nvidia.com/gpu and the HAMi node-nvidia-register annotation. That needs
-- both the operator and the api running.

-- A billed GPU offering. An exclusive session has occupancy 1.0, so the hold and consumption both
-- equal credit_per_hour, here 10.
INSERT INTO offering (id, name, resource_class, gpu_model, gpu_mem_mb, gpu_cores, credit_per_hour)
VALUES ('off_gpu_excl', 'RTX4090 Exclusive', 'gpu', 'NVIDIA-RTX-4090', 24576, 100, 10)
ON CONFLICT (id) DO NOTHING;

-- Credit wallet owned by usr_admin: balance 1000, nothing reserved.
INSERT INTO credit_wallet (id, owner_type, owner_id, balance, reserved, version)
VALUES ('wal_demo', 'user', 'usr_admin', 1000, 0, 0)
ON CONFLICT (id) DO NOTHING;
