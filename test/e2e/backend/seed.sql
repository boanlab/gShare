-- Reference data for the API-to-custom-resource end-to-end test with a CPU session: the minimum set
-- of foreign key targets.
-- A Session references a user (its owner), a cluster, an offering, and an image. project_id and
-- billing_wallet_id may be NULL for a CPU session.
-- Idempotent through ON CONFLICT DO NOTHING.

-- The bootstrap super_admin. Its id has to match the `sub` claim of the development token, or the
-- owner foreign key will not resolve.
INSERT INTO "user" (id, email, name, status, global_role)
VALUES ('usr_admin', 'admin@example.com', 'Admin', 'active', 'super_admin')
ON CONFLICT (id) DO NOTHING;

-- Registering the in-cluster cluster: with api_server and kubeconfig_secret_ref both empty, crd.py
-- falls back to the in-cluster service account.
INSERT INTO cluster (id, name, role, api_server, runtime, status, kubeconfig_secret_ref)
VALUES ('clu_fake', 'fake-lab', 'primary', '', 'containerd', 'connected', '')
ON CONFLICT (id) DO NOTHING;

-- A free CPU offering: credit_per_hour 0, which bypasses billing and the credit hold.
INSERT INTO offering (id, name, resource_class, credit_per_hour)
VALUES ('off_cpu_free', 'CPU Free', 'cpu', 0)
ON CONFLICT (id) DO NOTHING;

-- The image id doubles as the custom resource's image string (the _image_ref fallback), so it has to
-- be a pullable, non-root reference.
INSERT INTO image (id, name, tags, kind)
VALUES ('nginxinc/nginx-unprivileged:alpine', 'nginx-unpriv', '{}'::jsonb, 'container')
ON CONFLICT (id) DO NOTHING;
