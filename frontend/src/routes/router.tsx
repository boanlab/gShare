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
import { VolumePage, NewVolumePage, VolumeSharePage, VolumeQuotaPage, VolumeSnapshotsPage } from '@/features/volume/VolumePage';
import { WalletPage, CreditRequestPage } from '@/features/wallet/WalletPage';
import { AccountPage } from '@/features/account/AccountPage';
import { PasswordPage } from '@/features/account/PasswordPage';

import { AdminDashboard } from '@/features/admin/Dashboard';
import { AdminOrgs, NewOrgPage, EditOrgPage, OrgAdminsPage } from '@/features/admin/Orgs';
import { AdminUsers, EditUserPage, NewUserPage, DeleteUserPage } from '@/features/admin/Users';
import { AdminGroups, NewGroupPage, EditGroupPage, GroupAdminsPage, DeleteGroupPage } from '@/features/admin/Groups';
import { AdminResources, EditOfferingPage, EditPresetPage, EditPolicyPage, CreateOfferingPage, CreatePresetPage, CreatePolicyPage } from '@/features/admin/Resources';
import { AdminClusters, EditClusterPage, NewClusterPage } from '@/features/admin/Clusters';
import { AdminNodes, DrainNodePage, NodeDevicesPage } from '@/features/admin/Nodes';
import { AdminMonitor, ForceTerminatePage } from '@/features/admin/Monitor';
import { AdminAudit } from '@/features/admin/Audit';
import { AdminImages, ImportImagePage, BuildImagePage } from '@/features/admin/Images';
import { AdminCreditAllocation } from '@/features/admin/CreditAllocation';

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
        { path: 'data/new', element: <NewVolumePage /> },
        { path: 'data/:volumeId/share', element: <VolumeSharePage /> },
        { path: 'data/:volumeId/quota', element: <VolumeQuotaPage /> },
        { path: 'data/:volumeId/snapshots', element: <VolumeSnapshotsPage /> },
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
            { path: 'orgs/new', element: <RequireRole role="super_admin"><NewOrgPage /></RequireRole> },
            { path: 'orgs/:orgId/edit', element: <RequireRole role="super_admin"><EditOrgPage /></RequireRole> },
            { path: 'orgs/:orgId/admins', element: <RequireRole role="super_admin"><OrgAdminsPage /></RequireRole> },
            { path: 'users', element: <RequireRole min="group_admin"><AdminUsers /></RequireRole> },
            { path: 'users/new', element: <RequireRole min="group_admin"><NewUserPage /></RequireRole> },
            { path: 'users/:userId/edit', element: <RequireRole min="group_admin"><EditUserPage /></RequireRole> },
            { path: 'users/:userId/delete', element: <RequireRole min="group_admin"><DeleteUserPage /></RequireRole> },
            { path: 'groups', element: <RequireRole min="group_admin"><AdminGroups /></RequireRole> },
            { path: 'groups/new', element: <RequireRole min="group_admin"><NewGroupPage /></RequireRole> },
            { path: 'groups/:groupId/edit', element: <RequireRole min="group_admin"><EditGroupPage /></RequireRole> },
            { path: 'groups/:groupId/admins', element: <RequireRole min="group_admin"><GroupAdminsPage /></RequireRole> },
            { path: 'groups/:groupId/delete', element: <RequireRole min="group_admin"><DeleteGroupPage /></RequireRole> },
            { path: 'resources', element: <RequireRole role="super_admin"><AdminResources /></RequireRole> },
            { path: 'resources/offerings/new', element: <RequireRole role="super_admin"><CreateOfferingPage /></RequireRole> },
            { path: 'resources/offerings/:offeringId/edit', element: <RequireRole role="super_admin"><EditOfferingPage /></RequireRole> },
            { path: 'resources/presets/new', element: <RequireRole role="super_admin"><CreatePresetPage /></RequireRole> },
            { path: 'resources/presets/:presetId/edit', element: <RequireRole role="super_admin"><EditPresetPage /></RequireRole> },
            { path: 'resources/policies/new', element: <RequireRole role="super_admin"><CreatePolicyPage /></RequireRole> },
            { path: 'resources/policies/:policyId/edit', element: <RequireRole role="super_admin"><EditPolicyPage /></RequireRole> },
            { path: 'clusters', element: <RequireRole role="super_admin"><AdminClusters /></RequireRole> },
            { path: 'clusters/new', element: <RequireRole role="super_admin"><NewClusterPage /></RequireRole> },
            { path: 'clusters/:clusterId/edit', element: <RequireRole role="super_admin"><EditClusterPage /></RequireRole> },
            { path: 'nodes', element: <RequireRole role="super_admin"><AdminNodes /></RequireRole> },
            { path: 'nodes/:nodeId/drain', element: <RequireRole role="super_admin"><DrainNodePage /></RequireRole> },
            { path: 'nodes/:nodeId/devices', element: <RequireRole role="super_admin"><NodeDevicesPage /></RequireRole> },
            { path: 'allocations', element: <AdminCreditAllocation /> }, // credit allocation and requests, group_admin and above
            { path: 'monitor', element: <AdminMonitor /> },
            { path: 'monitor/sessions/:sessionId/terminate', element: <ForceTerminatePage /> },
            { path: 'audit', element: <RequireRole min="group_admin"><AdminAudit /></RequireRole> },
            { path: 'images', element: <RequireRole role="super_admin"><AdminImages /></RequireRole> },
            { path: 'images/import', element: <RequireRole role="super_admin"><ImportImagePage /></RequireRole> },
            { path: 'images/build', element: <RequireRole role="super_admin"><BuildImagePage /></RequireRole> },
          ],
        },
      ],
    },

    { path: '/403', element: <Forbidden /> },
    { path: '/error', element: <SystemError /> },
    { path: '*', element: <NotFound /> },
  ],
);
