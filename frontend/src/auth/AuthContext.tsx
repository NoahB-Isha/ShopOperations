import { createContext, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { api, getToken, setToken } from "../api/client";
import type { UserOut } from "../api/types";

interface AuthState {
  user: UserOut | null;
  loading: boolean;
  roles: Set<string>;
  /** true when every one of this user's scoped roles sits in a departments
   *  review zone — drives "department" vs "center" copy */
  isDepartments: boolean;
  signIn: (token: string, user: UserOut) => void;
  signOut: () => void;
}

const AuthContext = createContext<AuthState>({
  user: null,
  loading: true,
  roles: new Set(),
  isDepartments: false,
  signIn: () => {},
  signOut: () => {},
});

function departmentsWording(roles: { role: string; zone_kind?: string | null }[]): boolean {
  if (roles.some((r) => r.zone_kind === "departments")) return true;
  if (!roles.some((r) => r.role === "dept_order_approver")) return false;
  return !roles.some((r) => r.role === "zone_coordinator" && r.zone_kind !== "departments");
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<UserOut | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!getToken()) {
      setLoading(false);
      return;
    }
    api<UserOut>("/auth/me")
      .then(setUser)
      .catch(() => setToken(null))
      .finally(() => setLoading(false));
  }, []);

  const value = useMemo<AuthState>(
    () => ({
      user,
      loading,
      roles: new Set(user?.roles.map((r) => r.role) ?? []),
      // Departments people are ordinary reviewers/requesters whose review zone
      // is III Departments — the wording follows the zone, never the role.
      // The one exception is the "Approve dept orders" add-on, which carries
      // no zone at all: everything IT can reach is a department, so it says
      // "department" too — unless the same person also reviews a field zone,
      // where that would be a lie.
      isDepartments: departmentsWording(user?.roles ?? []),
      signIn: (token, u) => {
        setToken(token);
        setUser(u);
      },
      signOut: () => {
        setToken(null);
        setUser(null);
      },
    }),
    [user, loading],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  return useContext(AuthContext);
}
