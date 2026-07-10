import { useState } from "react";
import {
  Badge,
  Button,
  Card,
  DataTable,
  Dialog,
  Drawer,
  EmptyState,
  Fab,
  Field,
  Input,
  PageHeader,
  Select,
  Spinner,
  Stat,
  StatusDot,
  Toggle,
  toneForLabel,
  useToast,
} from "../design";
import type { Column } from "../design";

/* ---- color roles ---------------------------------------------------- */
const roleSwatches = [
  ["primary", "bg-primary text-on-primary"],
  ["primary-container", "bg-primary-container text-on-primary-container"],
  ["secondary", "bg-secondary text-on-secondary"],
  ["secondary-container", "bg-secondary-container text-on-secondary-container"],
  ["tertiary", "bg-tertiary text-on-tertiary"],
  ["tertiary-container", "bg-tertiary-container text-on-tertiary-container"],
  ["error", "bg-error text-on-error"],
  ["error-container", "bg-error-container text-on-error-container"],
  ["success", "bg-success text-on-success"],
  ["success-container", "bg-success-container text-on-success-container"],
  ["warn", "bg-warn text-on-warn"],
  ["warn-container", "bg-warn-container text-on-warn-container"],
  ["inverse-surface", "bg-inverse-surface text-inverse-on-surface"],
  ["outline", "bg-outline text-surface"],
] as const;

const surfaceLadder = [
  ["surface", "bg-surface"],
  ["container-lowest", "bg-surface-container-lowest"],
  ["container-low", "bg-surface-container-low"],
  ["container", "bg-surface-container"],
  ["container-high", "bg-surface-container-high"],
  ["container-highest", "bg-surface-container-highest"],
] as const;

interface DemoRow {
  sku: string;
  name: string;
  category: string;
  qty: number;
  price: number;
}

const demoRows: DemoRow[] = [
  { sku: "CA0023000009", name: "Copper Water Bottle — 950ml", category: "Copper", qty: 120, price: 34 },
  { sku: "RU0000000005", name: "Rudraksha Mala — 5mm", category: "Rudraksha", qty: 2, price: 24 },
  { sku: "IN0000000777", name: "Sandalwood Incense", category: "Incense & Dhoop", qty: 0, price: 9 },
  { sku: "BL0000000021", name: "Bloom Ghee", category: "Bloom", qty: 46, price: 18 },
];

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mb-12">
      <h2 className="headline mb-5">{title}</h2>
      {children}
    </section>
  );
}

export function StyleguidePage() {
  const toast = useToast();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [toggled, setToggled] = useState(true);
  const [untoggled, setUntoggled] = useState(false);

  const columns: Column<DemoRow>[] = [
    { key: "sku", header: "SKU", sortable: true,
      render: (r) => <span className="font-mono text-[12.5px]">{r.sku}</span> },
    { key: "name", header: "Product", sortable: true },
    { key: "category", header: "Category",
      render: (r) => <Badge tone={toneForLabel(r.category)}>{r.category}</Badge> },
    { key: "qty", header: "On hand", align: "right", sortable: true },
    { key: "price", header: "Price", align: "right", sortable: true,
      render: (r) => `$${r.price.toFixed(2)}` },
  ];

  return (
    <>
      <PageHeader
        title="Styleguide"
        subtitle="Material 3, warmed up — color roles, type, and every core component, rendered live."
      />

      <Section title="Color roles">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-7">
          {roleSwatches.map(([name, cls]) => (
            <div
              key={name}
              className={`flex h-16 items-end rounded-(--radius-md) p-2 text-[10.5px] font-semibold ${cls}`}
            >
              {name}
            </div>
          ))}
        </div>
        <div className="mt-3 flex overflow-hidden rounded-(--radius-md) border border-outline-variant">
          {surfaceLadder.map(([name, cls]) => (
            <div key={name} className={`flex-1 px-2 py-4 text-center text-[10px] text-on-surface-variant ${cls}`}>
              {name}
            </div>
          ))}
        </div>
      </Section>

      <Section title="Type">
        <div className="flex flex-col gap-4">
          <div className="display-xl text-primary">Big, bold, a little wonky.</div>
          <div className="display-l">Display large fronts every page.</div>
          <div className="headline text-on-surface-variant">
            Headlines keep the quirk; everything else keeps the peace.
          </div>
          <p className="max-w-xl text-sm text-on-surface">
            Inter handles everything operational: tables, forms, and long labels. Body text stays
            at 15px with relaxed leading so dense pages read calmly — the punch lives in the
            display scale, not in the data.
          </p>
          <p className="text-[13px] text-on-surface-variant">
            13px secondary text carries hints, timestamps, and detail.
          </p>
          <span className="label-m">Label medium structures tables & fields</span>
          <span className="font-mono text-[13px]">CA0023000009 · monospace for SKUs and references</span>
        </div>
      </Section>

      <Section title="Buttons">
        <div className="flex flex-wrap items-center gap-2.5">
          <Button>Filled</Button>
          <Button variant="secondary">Tonal</Button>
          <Button variant="outlined">Outlined</Button>
          <Button variant="elevated">Elevated</Button>
          <Button variant="ghost">Text</Button>
          <Button variant="danger">Danger</Button>
          <Button loading>Saving…</Button>
          <Button disabled>Disabled</Button>
          <Button size="sm">Small</Button>
          <Fab label="New item" onClick={() => toast.info("FABs hold the page's one big action.")} />
        </div>
      </Section>

      <Section title="Chips & status">
        <div className="flex flex-wrap items-center gap-2.5">
          <Badge>neutral</Badge>
          <Badge tone="copper">Air only</Badge>
          <Badge tone="forest">fresh</Badge>
          <Badge tone="gold">stale</Badge>
          <Badge tone="danger">Expires 2026-09-01</Badge>
          <Badge tone="secondary">secondary</Badge>
          <Badge tone="tertiary">tertiary</Badge>
          <Badge tone="outline">untracked</Badge>
          <StatusDot ok label="Synced · live Odoo" />
          <StatusDot ok={false} warn label="Sync stale" />
          <StatusDot ok={false} label="Odoo auth failing!" />
          <Spinner />
        </div>
        <p className="mt-3 text-[13px] text-on-surface-variant">
          Category chips pick their color by name hash — same label, same color, every time:
        </p>
        <div className="mt-2 flex flex-wrap gap-2">
          {["Copper", "Rudraksha", "Bloom", "Incense & Dhoop", "Snacks", "Books & Media"].map((c) => (
            <Badge key={c} tone={toneForLabel(c)}>{c}</Badge>
          ))}
        </div>
      </Section>

      <Section title="Forms">
        <Card className="max-w-md" variant="elevated">
          <div className="flex flex-col gap-4">
            <Field label="Email or phone" help="Filled fields float their labels.">
              <Input placeholder="you@example.org" />
            </Field>
            <Field label="Display name">
              <Input defaultValue="Sachi Mutluru" />
            </Field>
            <Field label="Zone" error="Pick a zone for this coordinator.">
              <Select defaultValue="">
                <option value="" disabled>Choose…</option>
                <option>Zone 1 (Lili)</option>
                <option>Canada</option>
              </Select>
            </Field>
            <div className="flex items-center gap-6">
              <Toggle checked={toggled} onChange={setToggled} label="Dept-orderable" />
              <Toggle checked={untoggled} onChange={setUntoggled} label="Off" />
            </div>
            <Input
              aria-label="Pill search"
              placeholder="Pill search fields for tables…"
              className="[--control-radius:9999px]"
            />
          </div>
        </Card>
      </Section>

      <Section title="Stats">
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Stat label="Odoo connection" value="Fixture" hint="no credentials set" />
          <Stat label="Writes" value="Kill switch OFF" hint="all writes dry-run" tone="good" />
          <Stat label="Sync age" value="12m" tone="warn" />
          <Stat label="Auth failures" value="1" tone="bad" />
        </div>
      </Section>

      <Section title="Data table">
        <DataTable
          columns={columns}
          rows={demoRows}
          rowKey={(r) => r.sku}
          onRowClick={() => toast.info("Row clicked.")}
          footer={<span>{demoRows.length} rows · click headers to sort</span>}
        />
      </Section>

      <Section title="Feedback">
        <div className="flex flex-wrap gap-2.5">
          <Button variant="secondary" onClick={() => toast.success("Draft transfer created — review it in Odoo.")}>
            Success snackbar
          </Button>
          <Button variant="secondary" onClick={() => toast.error("Odoo rejected the transfer: missing picking type.")}>
            Error snackbar
          </Button>
          <Button variant="secondary" onClick={() => toast.info("Sync started.")}>
            Info snackbar
          </Button>
          <Button variant="secondary" onClick={() => setDialogOpen(true)}>Dialog</Button>
          <Button variant="secondary" onClick={() => setDrawerOpen(true)}>Side sheet</Button>
        </div>
      </Section>

      <Section title="Empty state">
        <EmptyState
          title="No orders yet"
          hint="When city centers start ordering in Phase 3, their requests land here for approval."
          action={<Button variant="secondary" size="sm">Learn what's coming</Button>}
        />
      </Section>

      <Dialog
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
        title="Run the live canary?"
        footer={
          <>
            <Button variant="ghost" onClick={() => setDialogOpen(false)}>Cancel</Button>
            <Button onClick={() => setDialogOpen(false)}>Confirm</Button>
          </>
        }
      >
        <p className="text-sm leading-6 text-on-surface-variant">
          Dialogs confirm consequential actions. 28dp corners, emphasized easing, Escape or
          backdrop to dismiss — the primary action sits bottom-right.
        </p>
      </Dialog>

      <Drawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        title="Copper Water Bottle — 950ml"
        footer={<Button onClick={() => setDrawerOpen(false)}>Close</Button>}
      >
        <p className="text-sm leading-6 text-on-surface-variant">
          Side sheets hold detail views — product records, order lines — sliding in with
          emphasized easing without losing the table behind.
        </p>
      </Drawer>
    </>
  );
}
