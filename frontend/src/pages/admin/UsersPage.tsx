import { usePersistedState } from "../../persist";
import { useMemo, useState } from "react";
import { useCenters, useInviteUser, useUpdateUser, useUsers, useZones } from "../../api/hooks";
import type { UserOut } from "../../api/types";
import {
  Badge,
  Button,
  DataTable,
  Dialog,
  Field,
  Input,
  PageHeader,
  Select,
  useToast,
} from "../../design";
import type { Column } from "../../design";
import { useSillyLabel } from "../../silly";

const ROLE_LABELS: Record<string, string> = {
  admin: "Admin",
  warehouse: "Warehouse",
  shoppe_floor: "Shoppe floor",
  floor_rotating: "Floor (rotating — no transfer creation)",
  zone_coordinator: "Zone coordinator",
  center_orderer: "Center orderer",
  dept_liaison: "Dept liaison",
  dept_orderer: "Dept orderer",
};

const ZONE_ROLES = new Set(["zone_coordinator", "dept_liaison"]);
const CENTER_ROLES = new Set(["center_orderer", "dept_orderer"]);

export function UsersPage() {
  const { data: users, isLoading } = useUsers();
  const update = useUpdateUser();
  const toast = useToast();
  const [filter, setFilter] = usePersistedState("users.filter", "");
  const s = useSillyLabel();
  const [inviteOpen, setInviteOpen] = useState(false);
  const [editing, setEditing] = useState<UserOut | null>(null);

  const columns = useMemo<Column<UserOut>[]>(
    () => [
      {
        key: "display_name",
        header: "Name",
        sortable: true,
        render: (u) => (
          <span className={u.is_active ? "font-medium" : "text-ink-faint line-through"}>
            {u.display_name || "—"}
          </span>
        ),
      },
      {
        key: "contact",
        header: "Contact",
        value: (u) => u.email ?? u.phone ?? "",
        render: (u) => (
          <span className="text-ink-soft">
            {u.email ?? u.phone}
            {u.email && u.phone && <span className="text-ink-faint"> · {u.phone}</span>}
          </span>
        ),
      },
      {
        key: "roles",
        header: "Roles",
        render: (u) => (
          <span className="flex flex-wrap gap-1">
            {u.roles.map((r, i) => (
              <Badge key={i} tone={r.role === "admin" ? "copper" : "neutral"}>
                {ROLE_LABELS[r.role] ?? r.role}
                {r.zone_name ? ` · ${r.zone_name}` : r.center_name ? ` · ${r.center_name}` : ""}
              </Badge>
            ))}
            {u.roles.length === 0 && <span className="text-ink-faint">no roles</span>}
          </span>
        ),
      },
      {
        key: "actions",
        header: "",
        align: "right",
        render: (u) => (
          <span className="flex justify-end gap-1">
            <Button
              variant="ghost"
              size="sm"
              onClick={(e) => {
                e.stopPropagation();
                setEditing(u);
              }}
            >
              Edit
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={(e) => {
                e.stopPropagation();
                update.mutate(
                  { id: u.id, is_active: !u.is_active },
                  {
                    onSuccess: () =>
                      toast.info(`${u.display_name || u.email} ${u.is_active ? "deactivated" : "reactivated"}.`),
                    onError: (err) => toast.error(err.message),
                  },
                );
              }}
            >
              {u.is_active ? "Deactivate" : "Reactivate"}
            </Button>
          </span>
        ),
      },
    ],
    [update, toast],
  );

  return (
    <>
      <PageHeader
        title="Users"
        subtitle="Who can sign in, and what each person can see."
        actions={<Button onClick={() => setInviteOpen(true)}>Invite user</Button>}
      />
      <div className="mb-4">
        <Input
          placeholder={s("Filter by name, email, phone…")}
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="max-w-xs"
        />
      </div>
      <DataTable
        columns={columns}
        rows={users ?? []}
        rowKey={(u) => u.id}
        loading={isLoading}
        filterText={filter}
        footer={<span>{users?.length ?? 0} users</span>}
      />
      <InviteDialog open={inviteOpen} onClose={() => setInviteOpen(false)} />
      {/* Mounted only while editing, and keyed by user: the draft state seeds
          from props on mount, so switching rows can't leak one user's edits
          into another's form. */}
      {editing && (
        <EditUserDialog key={editing.id} user={editing} onClose={() => setEditing(null)} />
      )}
    </>
  );
}

type RoleDraft = { role: string; zone_id: string; center_id: string };

/** Edit contact details and roles. Roles are a full replacement server-side and
 *  a user may legitimately hold several (coordinator of a zone AND orderer at a
 *  center), so this edits the whole set — a single-role picker here would
 *  quietly drop the others on save. */
function EditUserDialog({ user, onClose }: { user: UserOut; onClose: () => void }) {
  const update = useUpdateUser();
  const { data: zones } = useZones();
  const { data: centers } = useCenters();
  const toast = useToast();

  const [name, setName] = useState(user.display_name);
  const [email, setEmail] = useState(user.email ?? "");
  const [phone, setPhone] = useState(user.phone ?? "");
  const [roles, setRoles] = useState<RoleDraft[]>(
    user.roles.map((r) => ({
      role: r.role,
      zone_id: r.zone_id ? String(r.zone_id) : "",
      center_id: r.center_id ? String(r.center_id) : "",
    })),
  );
  const [error, setError] = useState("");

  const patch = (i: number, next: Partial<RoleDraft>) =>
    setRoles((rs) => rs.map((r, j) => (j === i ? { ...r, ...next } : r)));

  // Mirrors _validate_role_scopes on the server, so a missing scope is caught
  // here instead of coming back as a 422 after the round-trip.
  const incomplete = roles.some(
    (r) => (ZONE_ROLES.has(r.role) && !r.zone_id) || (CENTER_ROLES.has(r.role) && !r.center_id),
  );
  const noContact = !email.trim() && !phone.trim();
  // The server normalizes rather than rejects: normalize_email("nope") is None
  // and normalize_phone("123") is None, so a typo would silently CLEAR the
  // field instead of erroring — in the one screen meant to fix contact info.
  // These mirror those rules so a bad value is caught before it costs data.
  const emailInvalid = email.trim() !== "" && !email.includes("@");
  const phoneInvalid = phone.trim() !== "" && phone.replace(/\D/g, "").length < 8;
  const rolesChanged =
    JSON.stringify(roles) !==
    JSON.stringify(
      user.roles.map((r) => ({
        role: r.role,
        zone_id: r.zone_id ? String(r.zone_id) : "",
        center_id: r.center_id ? String(r.center_id) : "",
      })),
    );

  const submit = () => {
    setError("");
    update.mutate(
      {
        id: user.id,
        display_name: name,
        // "" clears (normalize_* turns it into NULL); null would mean
        // "leave unchanged" and clearing a field would silently do nothing.
        email: email.trim(),
        phone: phone.trim(),
        // Only send roles when they actually changed. The server revokes every
        // session whenever a role list arrives (old sessions must not keep old
        // permissions), so sending them unconditionally would sign someone out
        // for a typo fix in their name.
        ...(rolesChanged
          ? {
              roles: roles.map((r) => ({
                role: r.role,
                zone_id: ZONE_ROLES.has(r.role) && r.zone_id ? Number(r.zone_id) : null,
                center_id: CENTER_ROLES.has(r.role) && r.center_id ? Number(r.center_id) : null,
              })),
            }
          : {}),
      },
      {
        onSuccess: (u) => {
          toast.success(`${u.display_name || u.email || u.phone} updated.`);
          onClose();
        },
        onError: (e) => setError(e.message),
      },
    );
  };

  return (
    <Dialog
      open
      onClose={onClose}
      title={`Edit ${user.display_name || user.email || user.phone}`}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            onClick={submit}
            loading={update.isPending}
            disabled={noContact || incomplete || emailInvalid || phoneInvalid}
          >
            Save changes
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <Field label="Display name">
          <Input value={name} onChange={(e) => setName(e.target.value)} autoFocus />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field
            label="Email"
            help={noContact ? "one of these is required" : "used for sign-in"}
            error={emailInvalid ? "That doesn't look like an email address." : ""}
          >
            <Input value={email} onChange={(e) => setEmail(e.target.value)} />
          </Field>
          <Field
            label="Phone"
            error={phoneInvalid ? "That doesn't look like a phone number." : ""}
          >
            <Input value={phone} onChange={(e) => setPhone(e.target.value)} />
          </Field>
        </div>

        <div className="flex flex-col gap-2">
          <span className="text-[13px] font-medium text-on-surface-variant">Roles</span>
          {roles.length === 0 && (
            <span className="text-[13px] text-on-surface-variant">
              No roles — they can sign in but will land on an empty app.
            </span>
          )}
          {roles.map((r, i) => (
            <div key={i} className="flex flex-col gap-2 rounded-(--radius-md) bg-surface-container-low p-3">
              <div className="flex items-center gap-2">
                <Select
                  value={r.role}
                  onChange={(e) =>
                    // Scope belongs to the role that needed it; changing the
                    // role clears it so a stale zone can't ride along.
                    patch(i, { role: e.target.value, zone_id: "", center_id: "" })
                  }
                  className="flex-1"
                >
                  {Object.entries(ROLE_LABELS).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </Select>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setRoles((rs) => rs.filter((_, j) => j !== i))}
                >
                  Remove
                </Button>
              </div>
              {ZONE_ROLES.has(r.role) && (
                <Select value={r.zone_id} onChange={(e) => patch(i, { zone_id: e.target.value })}>
                  <option value="">Choose a zone…</option>
                  {zones?.map((z) => (
                    <option key={z.id} value={z.id}>
                      {z.name}
                    </option>
                  ))}
                </Select>
              )}
              {CENTER_ROLES.has(r.role) && (
                <Select
                  value={r.center_id}
                  onChange={(e) => patch(i, { center_id: e.target.value })}
                >
                  <option value="">Choose a center…</option>
                  {centers?.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </Select>
              )}
            </div>
          ))}
          <Button
            variant="outlined"
            size="sm"
            onClick={() =>
              setRoles((rs) => [...rs, { role: "center_orderer", zone_id: "", center_id: "" }])
            }
          >
            Add a role
          </Button>
        </div>

        {rolesChanged && (
          <p className="text-[12.5px] leading-5 text-on-surface-variant">
            Changing roles signs this person out on every device — they'll sign in again and
            pick up the new permissions.
          </p>
        )}
        {error && (
          <p role="alert" className="text-[13px] text-error">
            {error}
          </p>
        )}
      </div>
    </Dialog>
  );
}

function InviteDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const invite = useInviteUser();
  const { data: zones } = useZones();
  const { data: centers } = useCenters();
  const toast = useToast();

  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState("center_orderer");
  const [zoneId, setZoneId] = useState("");
  const [centerId, setCenterId] = useState("");
  const [error, setError] = useState("");

  const needsZone = ZONE_ROLES.has(role);
  const needsCenter = CENTER_ROLES.has(role);

  const submit = () => {
    setError("");
    invite.mutate(
      {
        email: email || undefined,
        phone: phone || undefined,
        display_name: name,
        roles: [
          {
            role,
            zone_id: needsZone && zoneId ? Number(zoneId) : undefined,
            center_id: needsCenter && centerId ? Number(centerId) : undefined,
          },
        ],
      },
      {
        onSuccess: (u) => {
          toast.success(`${u.display_name || u.email || u.phone} invited — they can sign in now.`);
          setEmail(""); setPhone(""); setName(""); setZoneId(""); setCenterId("");
          onClose();
        },
        onError: (e) => setError(e.message),
      },
    );
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Invite a user"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button
            onClick={submit}
            loading={invite.isPending}
            disabled={(!email && !phone) || (needsZone && !zoneId) || (needsCenter && !centerId)}
          >
            Send invite
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <Field label="Display name">
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Sachi Mutluru" autoFocus />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Email" help="either one works">
            <Input value={email} onChange={(e) => setEmail(e.target.value)} placeholder="s@example.org" />
          </Field>
          <Field label="Phone">
            <Input value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="(512) 555-0100" />
          </Field>
        </div>
        <Field label="Role" error={error}>
          <Select value={role} onChange={(e) => setRole(e.target.value)}>
            {Object.entries(ROLE_LABELS).map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </Select>
        </Field>
        {needsZone && (
          <Field label="Zone">
            <Select value={zoneId} onChange={(e) => setZoneId(e.target.value)}>
              <option value="">Choose a zone…</option>
              {zones?.map((z) => (
                <option key={z.id} value={z.id}>{z.name}</option>
              ))}
            </Select>
          </Field>
        )}
        {needsCenter && (
          <Field label="Center">
            <Select value={centerId} onChange={(e) => setCenterId(e.target.value)}>
              <option value="">Choose a center…</option>
              {centers?.map((c) => (
                <option key={c.id} value={c.id}>{c.name}</option>
              ))}
            </Select>
          </Field>
        )}
      </div>
    </Dialog>
  );
}
