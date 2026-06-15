-- Role seed for the end-to-end tests: super_admin, org_admin, group_admin, and member, plus the
-- catalogue and wallets.
-- Every password is 'Passw0rd!', with must_change_password false so UI login is a single step.

-- Organization and group
INSERT INTO organization (id,name,status,timezone) VALUES ('org_e2e','E2E Org','active','Asia/Seoul') ON CONFLICT (id) DO NOTHING;
INSERT INTO "group" (id,org_id,name,status) VALUES ('grp_e2e','org_e2e','E2E Group','active') ON CONFLICT (id) DO NOTHING;

-- The four users
INSERT INTO "user" (id,email,name,status,global_role,password_hash,must_change_password) VALUES
  ('usr_super','super@e2e.test','E2E Super','active','super_admin','pbkdf2_sha256$200000$UaQEoLlto5y7VdgANieC9g==$H/SRAZVaxswQEBqb+4G7HeHsBytKfo47dhRkwIONH5E=',false),
  ('usr_org','org@e2e.test','E2E OrgAdmin','active',NULL,'pbkdf2_sha256$200000$UaQEoLlto5y7VdgANieC9g==$H/SRAZVaxswQEBqb+4G7HeHsBytKfo47dhRkwIONH5E=',false),
  ('usr_grp','grp@e2e.test','E2E GroupAdmin','active',NULL,'pbkdf2_sha256$200000$UaQEoLlto5y7VdgANieC9g==$H/SRAZVaxswQEBqb+4G7HeHsBytKfo47dhRkwIONH5E=',false),
  ('usr_mem','member@e2e.test','E2E Member','active',NULL,'pbkdf2_sha256$200000$UaQEoLlto5y7VdgANieC9g==$H/SRAZVaxswQEBqb+4G7HeHsBytKfo47dhRkwIONH5E=',false)
ON CONFLICT (id) DO UPDATE SET password_hash=EXCLUDED.password_hash, global_role=EXCLUDED.global_role, must_change_password=false;

-- Memberships, which carry the role within a group. super_admin is global and needs none.
INSERT INTO membership (id,user_id,group_id,role) VALUES
  ('mbr_org','usr_org','grp_e2e','org_admin'),
  ('mbr_grp','usr_grp','grp_e2e','group_admin'),
  ('mbr_mem','usr_mem','grp_e2e','member')
ON CONFLICT (user_id,group_id) DO UPDATE SET role=EXCLUDED.role;

-- Catalogue for the session and queue tests, structured exactly as in seed.sql
INSERT INTO cluster (id,name,role,api_server,runtime,status,kubeconfig_secret_ref)
  VALUES ('clu_fake','fake-lab','primary','','containerd','connected','') ON CONFLICT (id) DO NOTHING;
INSERT INTO offering (id,name,resource_class,credit_per_hour)
  VALUES ('off_cpu_free','CPU Free','cpu',0) ON CONFLICT (id) DO NOTHING;
INSERT INTO offering (id,name,resource_class,gpu_model,gpu_mem_mb,gpu_cores,credit_per_hour)
  VALUES ('off_gpu_excl','RTX4090 Exclusive','gpu','NVIDIA-RTX-4090',24576,100,10) ON CONFLICT (id) DO NOTHING;
INSERT INTO image (id,name,tags,kind)
  VALUES ('nginxinc/nginx-unprivileged:alpine','nginx-unpriv','{}'::jsonb,'container') ON CONFLICT (id) DO NOTHING;

-- One wallet per user
INSERT INTO credit_wallet (id,owner_type,owner_id,balance,reserved,version) VALUES
  ('wal_super','user','usr_super',1000,0,0),
  ('wal_org','user','usr_org',1000,0,0),
  ('wal_grp','user','usr_grp',1000,0,0),
  ('wal_mem','user','usr_mem',500,0,0)
ON CONFLICT (id) DO NOTHING;
