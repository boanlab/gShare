// Every screen, with the lowest role that may reach it and the parameters filled from live data.
// `kind` selects which audits apply.
//
// Keep in step with frontend/src/routes/router.tsx.

export const ROUTES = [
  // ── unauthenticated ──
  { path: '/login', id: 'login', role: null, kind: 'form', title: 'Sign in' },
  { path: '/change-password', id: 'change-password', role: 'member', kind: 'form', title: 'Change password' },
  { path: '/403', id: 'forbidden', role: 'member', kind: 'message', title: 'Forbidden' },
  { path: '/error', id: 'system-error', role: 'member', kind: 'message', title: 'System error' },
  { path: '/no-such-page', id: 'not-found', role: 'member', kind: 'message', title: 'Not found' },

  // ── user console ──
  { path: '/', id: 'dashboard', role: 'member', kind: 'dashboard', title: 'Dashboard' },
  { path: '/sessions', id: 'sessions', role: 'member', kind: 'list', title: 'Sessions' },
  { path: '/sessions/new', id: 'session-wizard', role: 'member', kind: 'wizard', title: 'New session' },
  { path: '/sessions/{sessionId}', id: 'session-detail', role: 'member', kind: 'detail', title: 'Session detail' },
  { path: '/sessions/{sessionId}/connect', id: 'session-connect', role: 'member', kind: 'detail', title: 'Connect' },
  { path: '/queue', id: 'queue', role: 'member', kind: 'list', title: 'Queue' },
  { path: '/wallet', id: 'wallet', role: 'member', kind: 'list', title: 'Wallet' },
  { path: '/wallet/request', id: 'wallet-request', role: 'member', kind: 'form', title: 'Request credit' },
  { path: '/data', id: 'volumes', role: 'member', kind: 'list', title: 'Data' },
  { path: '/data/new', id: 'volume-new', role: 'member', kind: 'form', title: 'New volume' },
  { path: '/data/{volumeId}/share', id: 'volume-share', role: 'member', kind: 'form', title: 'Share volume' },
  { path: '/data/{volumeId}/quota', id: 'volume-quota', role: 'member', kind: 'form', title: 'Volume quota' },
  { path: '/data/{volumeId}/snapshots', id: 'volume-snapshots', role: 'member', kind: 'list', title: 'Snapshots' },
  { path: '/account', id: 'account', role: 'member', kind: 'form', title: 'Account' },
  { path: '/account/password', id: 'account-password', role: 'member', kind: 'form', title: 'Password' },

  // ── admin console ──
  { path: '/admin', id: 'admin-dashboard', role: 'group_admin', kind: 'dashboard', title: 'Admin dashboard' },
  { path: '/admin/users', id: 'admin-users', role: 'group_admin', kind: 'list', title: 'Users' },
  { path: '/admin/users/new', id: 'admin-user-new', role: 'group_admin', kind: 'form', title: 'New user' },
  { path: '/admin/users/{userId}/edit', id: 'admin-user-edit', role: 'group_admin', kind: 'form', title: 'Edit user' },
  { path: '/admin/users/{userId}/delete', id: 'admin-user-delete', role: 'group_admin', kind: 'destructive', title: 'Delete user' },
  { path: '/admin/groups', id: 'admin-groups', role: 'group_admin', kind: 'list', title: 'Groups' },
  { path: '/admin/groups/new', id: 'admin-group-new', role: 'group_admin', kind: 'form', title: 'New group' },
  { path: '/admin/groups/{groupId}/edit', id: 'admin-group-edit', role: 'group_admin', kind: 'form', title: 'Edit group' },
  { path: '/admin/groups/{groupId}/admins', id: 'admin-group-admins', role: 'group_admin', kind: 'form', title: 'Group admins' },
  { path: '/admin/groups/{groupId}/delete', id: 'admin-group-delete', role: 'group_admin', kind: 'destructive', title: 'Delete group' },
  { path: '/admin/allocations', id: 'admin-allocations', role: 'group_admin', kind: 'list', title: 'Allocations' },
  { path: '/admin/monitor', id: 'admin-monitor', role: 'group_admin', kind: 'list', title: 'Monitor' },
  { path: '/admin/audit', id: 'admin-audit', role: 'group_admin', kind: 'list', title: 'Audit log' },
  { path: '/admin/orgs', id: 'admin-orgs', role: 'super_admin', kind: 'list', title: 'Organizations' },
  { path: '/admin/orgs/new', id: 'admin-org-new', role: 'super_admin', kind: 'form', title: 'New organization' },
  { path: '/admin/orgs/{orgId}/edit', id: 'admin-org-edit', role: 'super_admin', kind: 'form', title: 'Edit organization' },
  { path: '/admin/orgs/{orgId}/admins', id: 'admin-org-admins', role: 'super_admin', kind: 'form', title: 'Organization admins' },
  { path: '/admin/resources', id: 'admin-resources', role: 'super_admin', kind: 'list', title: 'Resources' },
  { path: '/admin/resources/offerings/new', id: 'admin-offering-new', role: 'super_admin', kind: 'form', title: 'New offering' },
  { path: '/admin/resources/offerings/{offeringId}/edit', id: 'admin-offering-edit', role: 'super_admin', kind: 'form', title: 'Edit offering' },
  { path: '/admin/resources/presets/new', id: 'admin-preset-new', role: 'super_admin', kind: 'form', title: 'New preset' },
  { path: '/admin/resources/policies/new', id: 'admin-policy-new', role: 'super_admin', kind: 'form', title: 'New policy' },
  { path: '/admin/clusters', id: 'admin-clusters', role: 'super_admin', kind: 'list', title: 'Clusters' },
  { path: '/admin/clusters/new', id: 'admin-cluster-new', role: 'super_admin', kind: 'form', title: 'Register cluster' },
  { path: '/admin/nodes', id: 'admin-nodes', role: 'super_admin', kind: 'list', title: 'Nodes' },
  { path: '/admin/images', id: 'admin-images', role: 'super_admin', kind: 'list', title: 'Images' },
  { path: '/admin/images/import', id: 'admin-image-import', role: 'super_admin', kind: 'form', title: 'Import image' },
  { path: '/admin/images/build', id: 'admin-image-build', role: 'super_admin', kind: 'form', title: 'Build image' },
];

const RANK = ['member', 'group_admin', 'org_admin', 'super_admin'];

/** Routes this persona is entitled to reach, with {param} filled from `ids`. */
export function routesFor(persona, ids) {
  return ROUTES.filter((r) => {
    if (r.role === null) return persona.id === 'newcomer'; // only one persona audits the signed-out screens
    return RANK.indexOf(persona.role) >= RANK.indexOf(r.role);
  })
    .map((r) => {
      const path = r.path.replace(/\{(\w+)\}/g, (_, k) => ids[k] ?? '');
      return path.includes('//') || /\/$/.test(path.slice(1)) ? null : { ...r, path };
    })
    .filter(Boolean);
}
