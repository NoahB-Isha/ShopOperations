/* Edit a center, its roster, and the people who order for it — in one place.

   The roster used to be a spreadsheet the app re-read; it lives here now, so
   this dialog is where a name, a zone, a phone number or an orderer actually
   gets fixed. The import button at the bottom of the page still exists for
   bulk work, but it is something you bring TO the app, not its source of
   truth. */
import { useEffect, useState } from "react";
import { useInviteUser, useUpdateCenter, useZones } from "../../api/hooks";
import type { CenterOut, ContactOut } from "../../api/types";
import { Button, Dialog, Field, Input, Select, Toggle, useToast } from "../../design";
import { Icons } from "../../nav";

interface DraftContact extends ContactOut {
  key: string;
}

let contactKey = 0;
const withKeys = (contacts: ContactOut[]): DraftContact[] =>
  contacts.map((c) => ({ ...c, key: `c${contactKey++}` }));

const blankContact = (): DraftContact => ({
  key: `c${contactKey++}`,
  name: "",
  email: "",
  phone: "",
  role_note: "",
});

export function CenterEditDialog({
  center,
  onClose,
}: {
  center: CenterOut | null;
  onClose: () => void;
}) {
  const { data: zones } = useZones();
  const update = useUpdateCenter();
  const invite = useInviteUser();
  const toast = useToast();

  const [name, setName] = useState("");
  const [city, setCity] = useState("");
  const [state, setState] = useState("");
  const [zoneId, setZoneId] = useState("");
  const [active, setActive] = useState(true);
  const [terminal, setTerminal] = useState("");
  const [notes, setNotes] = useState("");
  const [contacts, setContacts] = useState<DraftContact[]>([]);
  const [newUser, setNewUser] = useState({ name: "", email: "", phone: "" });

  // Re-seed whenever a different center opens the dialog.
  useEffect(() => {
    if (!center) return;
    setName(center.name);
    setCity(center.city);
    setState(center.state);
    setZoneId(center.zone_id ? String(center.zone_id) : "");
    setActive(center.is_active);
    setTerminal(center.stripe_terminal_name);
    setNotes(center.notes);
    setContacts(withKeys(center.contacts));
    setNewUser({ name: "", email: "", phone: "" });
  }, [center]);

  if (!center) return null;

  const save = () => {
    update.mutate(
      {
        id: center.id,
        name: name.trim(),
        city: city.trim(),
        state: state.trim(),
        zone_id: zoneId ? Number(zoneId) : null,
        clear_zone: zoneId === "",
        is_active: active,
        stripe_terminal_name: terminal.trim(),
        notes,
        contacts: contacts.map(({ name: n, email, phone, role_note }) => ({
          name: n.trim(),
          email: email.trim(),
          phone: phone.trim(),
          role_note: role_note.trim(),
        })),
      },
      {
        onSuccess: () => {
          toast.success(`${name.trim()} saved.`);
          onClose();
        },
        onError: (e) => toast.error(e.message),
      },
    );
  };

  /** Invite an Order Requester scoped to this center — the whole point of
   *  "we can add users right here". They appear on the Users page too. */
  const addRequester = () => {
    const email = newUser.email.trim();
    if (!email) return;
    invite.mutate(
      {
        email,
        display_name: newUser.name.trim() || email,
        phone: newUser.phone.trim(),
        roles: [{ role: "center_orderer", center_id: center.id }],
      },
      {
        onSuccess: () => {
          toast.success(`${newUser.name.trim() || email} can now order for ${center.name}.`);
          setNewUser({ name: "", email: "", phone: "" });
        },
        onError: (e) => toast.error(e.message),
      },
    );
  };

  return (
    <Dialog
      open
      onClose={onClose}
      title={`Edit ${center.name}`}
      wide
      footer={
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={save} loading={update.isPending} disabled={!name.trim()}>
            Save changes
          </Button>
        </div>
      }
    >
      <div className="flex flex-col gap-6">
        <section className="grid items-start gap-x-3 gap-y-4 sm:grid-cols-2">
          <Field label="Center name">
            <Input value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <Field label="Review zone" help="Whose Order Reviewer approves its orders">
            <Select value={zoneId} onChange={(e) => setZoneId(e.target.value)}>
              <option value="">Unassigned</option>
              {zones?.map((z) => (
                <option key={z.id} value={z.id}>
                  {z.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="City">
            <Input value={city} onChange={(e) => setCity(e.target.value)} />
          </Field>
          <Field label="State / province">
            <Input value={state} onChange={(e) => setState(e.target.value)} />
          </Field>
          <Field label="Stripe terminal">
            <Input value={terminal} onChange={(e) => setTerminal(e.target.value)} />
          </Field>
          {/* NOT wrapped in Field: its floating label is drawn for a text input
              and lands on top of a switch. */}
          <div className="flex flex-col justify-center gap-1">
            <span className="text-[12px] font-medium text-ink-soft">Currently running</span>
            <Toggle
              checked={active}
              onChange={setActive}
              label={active ? "Sets up and sells" : "Dormant"}
            />
          </div>
        </section>

        <section>
          <SectionTitle icon={Icons.users} title="Roster" hint="People on this center's row" />
          <div className="flex flex-col gap-2">
            {contacts.length === 0 && (
              <p className="text-[13px] text-ink-faint">Nobody on the roster yet.</p>
            )}
            {contacts.map((c, i) => (
              <div key={c.key} className="grid gap-2 sm:grid-cols-[1.2fr_1.4fr_1fr_1fr_auto]">
                <Input
                  aria-label="Name"
                  placeholder="Name"
                  value={c.name}
                  onChange={(e) =>
                    setContacts((all) =>
                      all.map((x, j) => (i === j ? { ...x, name: e.target.value } : x)),
                    )
                  }
                />
                <Input
                  aria-label="Email"
                  placeholder="Email"
                  value={c.email}
                  onChange={(e) =>
                    setContacts((all) =>
                      all.map((x, j) => (i === j ? { ...x, email: e.target.value } : x)),
                    )
                  }
                />
                <Input
                  aria-label="Phone"
                  placeholder="Phone"
                  value={c.phone}
                  onChange={(e) =>
                    setContacts((all) =>
                      all.map((x, j) => (i === j ? { ...x, phone: e.target.value } : x)),
                    )
                  }
                />
                <Input
                  aria-label="Role"
                  placeholder="Role (Shoppe…)"
                  value={c.role_note}
                  onChange={(e) =>
                    setContacts((all) =>
                      all.map((x, j) => (i === j ? { ...x, role_note: e.target.value } : x)),
                    )
                  }
                />
                <button
                  onClick={() => setContacts((all) => all.filter((_, j) => j !== i))}
                  aria-label={`Remove ${c.name || "contact"}`}
                  className="state-layer grid h-10 w-10 place-items-center rounded-full text-ink-faint"
                >
                  <svg width="15" height="15" viewBox="0 0 16 16" fill="none">
                    <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
                  </svg>
                </button>
              </div>
            ))}
            <div>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setContacts((all) => [...all, blankContact()])}
              >
                + Add a person
              </Button>
            </div>
          </div>
        </section>

        <section>
          <SectionTitle
            icon={Icons.bag}
            title="Order Requesters"
            hint="App logins that can place this center's orders"
          />
          {center.requesters.length > 0 ? (
            <ul className="mb-3 flex flex-wrap gap-1.5">
              {center.requesters.map((r) => (
                <li
                  key={r}
                  className="rounded-full bg-surface-container px-2.5 py-1 text-[13px] text-ink-soft"
                >
                  {r}
                </li>
              ))}
            </ul>
          ) : (
            <p className="mb-3 text-[13px] text-ink-faint">
              Nobody can place orders for this center yet.
            </p>
          )}
          <div className="grid gap-2 sm:grid-cols-[1.2fr_1.4fr_1fr_auto]">
            <Input
              aria-label="New requester name"
              placeholder="Name"
              value={newUser.name}
              onChange={(e) => setNewUser((u) => ({ ...u, name: e.target.value }))}
            />
            <Input
              aria-label="New requester email"
              placeholder="Email"
              value={newUser.email}
              onChange={(e) => setNewUser((u) => ({ ...u, email: e.target.value }))}
            />
            <Input
              aria-label="New requester phone"
              placeholder="Phone (optional)"
              value={newUser.phone}
              onChange={(e) => setNewUser((u) => ({ ...u, phone: e.target.value }))}
            />
            <Button
              variant="secondary"
              onClick={addRequester}
              loading={invite.isPending}
              disabled={!newUser.email.trim()}
            >
              Invite
            </Button>
          </div>
          <p className="mt-1.5 text-[12px] text-ink-faint">
            They sign in with this email and see only this center's order form.
          </p>
        </section>

        <Field label="Notes">
          <Input value={notes} onChange={(e) => setNotes(e.target.value)} />
        </Field>
      </div>
    </Dialog>
  );
}

function SectionTitle({
  icon,
  title,
  hint,
}: {
  icon: React.ReactNode;
  title: string;
  hint: string;
}) {
  return (
    <div className="mb-2 flex items-baseline gap-2">
      <span className="text-ink-soft">{icon}</span>
      <span className="font-semibold text-ink">{title}</span>
      <span className="text-[12px] text-ink-faint">{hint}</span>
    </div>
  );
}
