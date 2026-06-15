import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { parseJwt, type JwtClaims } from '@/lib/jwt';
import { changePasswordRequest, passwordLogin } from '@/auth/authApi';
import { ROLE_ORDER } from '@/lib/rbac';

// Pick the highest role across every membership plus org_admin_orgs; gating uses that top rank.
function highestRole(roles: string[]): string | undefined {
  let best: string | undefined;
  let bestRank = -1;
  for (const r of roles) {
    const rank = ROLE_ORDER.indexOf(r as never);
    if (rank > bestRank) { bestRank = rank; best = r; }
  }
  return best;
}

// JWT-based authentication. Sessions are interactive only: there is no API key, CLI, or SDK path.
// The token is a 24-hour HS256 bearer with no refresh endpoint — on expiry a 401 logs the user out
// and they sign in again.

export interface Membership {
  group_id: string;
  project_name: string;
  role: string; // org_admin / group_admin / member ...
}

interface AuthState {
  accessToken?: string;
  claims: JwtClaims;
  memberships: Membership[];
  orgAdminOrgs: string[];        // organizations this user administers; authority survives even with no groups
  activeProjectId?: string;
  membershipRole?: string;       // highest rank across all memberships and org_admin_orgs; what gating uses
  isAuthed: boolean;
  /**
   * Whether /auth/me has answered this page load. Role gating outside super_admin depends on
   * memberships, which the token does not carry. Never persisted.
   */
  meLoaded: boolean;

  loginPassword(email: string, pw: string): Promise<void>;
  changePassword(newPw: string, currentPw?: string): Promise<void>;
  logout(reason?: string): void;
  setActiveProject(pid: string): void;
  setMeContext(m: Membership[], orgAdminOrgs: string[]): void;
  setMeLoaded(): void;
}

function applyTokens(set: (p: Partial<AuthState>) => void, access: string) {
  set({ accessToken: access, claims: parseJwt(access), isAuthed: true });
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      claims: {},
      memberships: [],
      orgAdminOrgs: [],
      isAuthed: false,
      meLoaded: false,

      async loginPassword(email, password) {
        const { access_token } = await passwordLogin(email, password);
        applyTokens(set, access_token);
      },

      async changePassword(newPw, currentPw) {
        const at = get().accessToken;
        if (!at) throw new Error('not authenticated');
        const { access_token } = await changePasswordRequest(at, newPw, currentPw);
        applyTokens(set, access_token);
      },

      logout() {
        set({
          accessToken: undefined,
          claims: {},
          memberships: [],
          orgAdminOrgs: [],
          isAuthed: false,
          activeProjectId: undefined,
          membershipRole: undefined,
          meLoaded: false,
        });
      },

      setActiveProject(pid) {
        // The active group only supplies the X-Project-Id header and the credit request context; it
        // never changes membershipRole, which is what gates the UI.
        set({ activeProjectId: pid });
      },

      setMeContext(memberships, orgAdminOrgs) {
        // Gating rank: the highest of every membership role, plus org_admin when org_admin_orgs is
        // non-empty.
        const roles = memberships.map((m) => m.role);
        if (orgAdminOrgs.length) roles.push('org_admin');
        set({ memberships, orgAdminOrgs, membershipRole: highestRole(roles), meLoaded: true });
      },
      // Also called when /auth/me fails.
      setMeLoaded() {
        set({ meLoaded: true });
      },
    }),
    {
      name: 'gshare-auth',
      // Survive a reload by persisting access, claims, and isAuthed. On expiry a 401 logs out.
      partialize: (s) => ({
        accessToken: s.accessToken,
        claims: s.claims,
        isAuthed: s.isAuthed,
        memberships: s.memberships,
        orgAdminOrgs: s.orgAdminOrgs,
        membershipRole: s.membershipRole,
        activeProjectId: s.activeProjectId,
      }),
    },
  ),
);
