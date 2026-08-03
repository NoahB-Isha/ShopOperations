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
  const [filter, setFilter] = useState("");
  const s = useSillyLabel();
  const [inviteOpen, setInviteOpen] = useState(false);

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
        ),
      },
    ],
    [update, toast],
  );

  return (
    <>
      <PageHeader
        title="Users"
        subtitle="Invites, roles, and scoping. Everyone signs in with one-time codes — no passwords to manage."
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
    </>
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
