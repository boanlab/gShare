import { createBrowserRouter } from 'react-router-dom';
import { RequireAuth } from '@/auth/RequireAuth';
import { RequireRole } from '@/auth/RequireRole';

import { Login, ChangePassword, Forbidden, NotFound, SystemError } from '@/features/auth/AuthPages';
import { Dashboard } from '@/features/Dashboard';
import { SessionList } from '@/features/session/SessionList';
import { SessionDetail } from '@/features/session/SessionDetail';
import { ConnectPage } from '@/features/session/ConnectModal';
import { SessionWizard } from '@/features/session/SessionWizard';
import { QueuePage } from '@/features/queue/QueuePage';
import { VolumePage } from '@/features/volume/VolumePage';
import { WalletPage, CreditRequestPage } from '@/features/wallet/WalletPage';
import { AccountPage } from '@/features/account/AccountPage';
import { NoticesPage, InquiriesPage, AdminNoticesPage, AdminInquiriesPage } from '@/features/boards/Boards';
import { PasswordPage } from '@/features/account/PasswordPage';

import { AdminDashboard } from '@/features/admin/Dashboard';
import { AdminOrgs, OrgAdminsPage } from '@/features/admin/Orgs';
import { AdminUsers, NewUserPage, DeleteUserPage } from '@/features/admin/Users';
import { UsersBulkImportPage } from '@/features/admin/UsersBulkImport';
import { AdminGroups, GroupAdminsPage, DeleteGroupPage } from '@/features/admin/Groups';
import { AdminResources, AdminPolicies, EditPolicyPage, CreatePolicyPage } from '@/features/admin/Resources';
import { AdminClusters } from '@/features/admin/Clusters';
import { AdminNodes, DrainNodePage, NodeDevicesPage } from '@/features/admin/Nodes';
import { AdminGpus } from '@/features/admin/Gpus';
import { AdminMonitoring } from '@/features/admin/Monitoring';
import { AdminMonitor, ForceTerminatePage } from '@/features/admin/Monitor';
import { AdminAudit } from '@/features/admin/Audit';
import { AdminImages } from '@/features/admin/Images';
import { AdminCreditAllocation } from '@/features/admin/CreditAllocation';
import { AdminVolumes } from '@/features/admin/Volumes';

// Route map. The SPA is served from / (see vite.config): the user console at / and the admin
// console at /admin.
export const router = createBrowserRouter(
  [
    { path: '/login', element: <Login /> },
    { path: '/change-password', element: <ChangePassword /> }, // forced password change at first login

    // ===== User console (/) =====
    {
      element: <RequireAuth variant="user" />, // JWT guard plus the user app shell
      children: [
        { index: true, element: <Dashboard /> },
        { path: 'sessions/new', element: <SessionWizard /> },
        { path: 'sessions', element: <SessionList /> },
        { path: 'sessions/:id', element: <SessionDetail /> },
        { path: 'sessions/:id/connect', element: <ConnectPage /> },
        { path: 'queue', element: <QueuePage /> },
        { path: 'wallet', element: <WalletPage /> },
        { path: 'wallet/request', element: <CreditRequestPage /> },
        { path: 'data', element: <VolumePage /> },
        { path: 'notices', element: <NoticesPage /> },
        { path: 'support', element: <InquiriesPage /> },
        { path: 'account', element: <AccountPage /> },
        { path: 'account/password', element: <PasswordPage /> },
      ],
    },

    // ===== Admin console (/admin), with its own entry point and layout =====
    {
      path: 'admin',
      element: <RequireAuth variant="admin" />, // JWT guard plus the admin app shell
      children: [
        {
          element: <RequireRole min="group_admin" />,
          children: [
            { index: true, element: <AdminDashboard /> },
            { path: 'orgs', element: <RequireRole role="super_admin"><AdminOrgs /></RequireRole> }, // organizations
            { path: 'orgs/:orgId/admins', element: <RequireRole role="super_admin"><OrgAdminsPage /></RequireRole> },
            { path: 'users', element: <RequireRole min="group_admin"><AdminUsers /></RequireRole> },
            { path: 'users/new', element: <RequireRole min="group_admin"><NewUserPage /></RequireRole> },
            { path: 'users/bulk', element: <RequireRole min="group_admin"><UsersBulkImportPage /></RequireRole> },
            { path: 'users/:userId/delete', element: <RequireRole min="group_admin"><DeleteUserPage /></RequireRole> },
            { path: 'groups', element: <RequireRole min="group_admin"><AdminGroups /></RequireRole> },
            { path: 'groups/:groupId/admins', element: <RequireRole min="group_admin"><GroupAdminsPage /></RequireRole> },
            { path: 'groups/:groupId/delete', element: <RequireRole min="group_admin"><DeleteGroupPage /></RequireRole> },
            { path: 'resources', element: <RequireRole role="super_admin"><AdminResources /></RequireRole> },
            { path: 'policies', element: <RequireRole min="group_admin"><AdminPolicies /></RequireRole> },
            { path: 'policies/new', element: <RequireRole role="super_admin"><CreatePolicyPage /></RequireRole> },
            { path: 'policies/:policyId/edit', element: <RequireRole role="super_admin"><EditPolicyPage /></RequireRole> },
            { path: 'clusters', element: <RequireRole role="super_admin"><AdminClusters /></RequireRole> },
            // org_admin reaches this page for the node-pools tab only (pool.read); the node inventory
            // itself is super_admin.
            { path: 'nodes', element: <RequireRole min="org_admin"><AdminNodes /></RequireRole> },
            { path: 'gpus', element: <RequireRole min="org_admin"><AdminGpus /></RequireRole> },
            { path: 'nodes/:nodeId/drain', element: <RequireRole role="super_admin"><DrainNodePage /></RequireRole> },
            { path: 'nodes/:nodeId/devices', element: <RequireRole role="super_admin"><NodeDevicesPage /></RequireRole> },
            { path: 'allocations', element: <AdminCreditAllocation /> }, // credit allocation and requests, group_admin and above
            { path: 'monitor', element: <AdminMonitor /> },
            { path: 'monitoring', element: <RequireRole role="super_admin"><AdminMonitoring /></RequireRole> },
            { path: 'monitor/sessions/:sessionId/terminate', element: <ForceTerminatePage /> },
            { path: 'audit', element: <RequireRole min="group_admin"><AdminAudit /></RequireRole> },
            { path: 'notices', element: <RequireRole min="group_admin"><AdminNoticesPage /></RequireRole> },
            { path: 'inquiries', element: <RequireRole min="group_admin"><AdminInquiriesPage /></RequireRole> },
            { path: 'images', element: <RequireRole role="super_admin"><AdminImages /></RequireRole> },
            { path: 'volumes', element: <RequireRole role="super_admin"><AdminVolumes /></RequireRole> },
          ],
        },
      ],
    },

    { path: '/403', element: <Forbidden /> },
    { path: '/error', element: <SystemError /> },
    { path: '*', element: <NotFound /> },
  ],
);
