// Personas the audit drives the console as: a real account and role, at the viewport and language
// that persona works in.
//
// Credentials come from the environment; the defaults are the throwaway accounts
// `hack/seed_demo.py` creates on a local compose stack. Override with UX_*_EMAIL / UX_*_PASSWORD.

const env = (k, d) => process.env[k] || d;

export const VIEWPORTS = {
  phone: { width: 390, height: 844 },
  tablet: { width: 820, height: 1180 },
  laptop: { width: 1280, height: 800 },
  desktop: { width: 1920, height: 1080 },
};

export const PERSONAS = [
  {
    id: 'platform-admin',
    // Runs the platform: clusters, nodes, the catalogue, images, audit.
    role: 'super_admin',
    email: env('UX_ADMIN_EMAIL', 'admin@example.com'),
    password: env('UX_ADMIN_PASSWORD', 'GshareUx!2026'),
    viewport: 'desktop',
    locale: 'en',
    goals: ['onboard a cluster', 'publish an offering', 'trace an action in the audit log'],
  },
  {
    id: 'org-admin',
    // Owns the organization budget: allocations, top-up decisions, group creation.
    role: 'org_admin',
    email: env('UX_ORGADMIN_EMAIL', 'jieun@nexusai.dev'),
    password: env('UX_ORGADMIN_PASSWORD', 'Nexus2026!'),
    viewport: 'laptop',
    locale: 'ko',
    goals: ['split the budget across teams', 'approve a top-up', 'explain a month of spend'],
  },
  {
    id: 'team-lead',
    // Group admin: adds members, sets quotas, watches who is burning credits.
    role: 'group_admin',
    email: env('UX_LEAD_EMAIL', 'minjun@nexusai.dev'),
    password: env('UX_LEAD_PASSWORD', 'Nexus2026!'),
    viewport: 'laptop',
    locale: 'ko',
    goals: ['add a member', 'cap a runaway session', 'see the team leaderboard'],
  },
  {
    id: 'researcher',
    // The everyday user: launches sessions, mounts data, reconnects to a notebook.
    role: 'member',
    email: env('UX_USER_EMAIL', 'haneul@nexusai.dev'),
    password: env('UX_USER_PASSWORD', 'Nexus2026!'),
    viewport: 'laptop',
    locale: 'en',
    goals: ['launch a session in under a minute', 'reattach after lunch', 'not run out of credit'],
  },
  {
    id: 'newcomer',
    // First sign-in: forced password change, no sessions, no volumes, no credit history.
    role: 'member',
    email: env('UX_NEW_EMAIL', 'woojin@nexusai.dev'),
    password: env('UX_NEW_PASSWORD', 'Nexus2026!'),
    firstLogin: true,
    viewport: 'laptop',
    locale: 'en',
    goals: ['understand what this is', 'find the one button that starts work'],
  },
  {
    id: 'mobile-user',
    // Checks on a running job from a phone; never creates anything.
    role: 'member',
    email: env('UX_MOBILE_EMAIL', 'seoyeon@nexusai.dev'),
    password: env('UX_MOBILE_PASSWORD', 'Nexus2026!'),
    viewport: 'phone',
    locale: 'ko',
    goals: ['is it still running', 'how much has it cost', 'stop it'],
  },
  {
    id: 'keyboard-user',
    // Keyboard only; the focus and tab-order audits run here.
    role: 'member',
    email: env('UX_KBD_EMAIL', 'dohyun@nexusai.dev'),
    password: env('UX_KBD_PASSWORD', 'Nexus2026!'),
    viewport: 'laptop',
    locale: 'en',
    keyboardOnly: true,
    goals: ['reach every control by Tab', 'escape every dialog', 'never lose focus'],
  },
  {
    id: 'tablet-lead',
    // A lead reviewing allocations on a tablet in a standup.
    role: 'group_admin',
    email: env('UX_LEAD_EMAIL', 'minjun@nexusai.dev'),
    password: env('UX_LEAD_PASSWORD', 'Nexus2026!'),
    viewport: 'tablet',
    locale: 'en',
    goals: ['skim the numbers', 'approve without a keyboard'],
  },
];

export const personaById = (id) => PERSONAS.find((p) => p.id === id);
