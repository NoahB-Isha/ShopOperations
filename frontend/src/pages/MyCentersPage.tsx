import { useCenters } from "../api/hooks";
import { useAuth } from "../auth/AuthContext";
import { Badge, Card, EmptyState, PageHeader, Spinner } from "../design";

export function MyCentersPage() {
  const { roles } = useAuth();
  const { data: centers, isLoading } = useCenters();
  const isDept = roles.has("dept_liaison") || roles.has("dept_orderer");
  const noun = isDept ? "departments" : "centers";

  return (
    <>
      <PageHeader
        title={isDept ? "My departments" : "My centers"}
        subtitle={`The ${noun} in your zone. Pending orders appear here in Phase 3.`}
      />
      {isLoading ? (
        <div className="grid place-items-center py-20"><Spinner size={22} /></div>
      ) : !centers?.length ? (
        <EmptyState
          title={`No ${noun} assigned yet`}
          hint="Ask the office to assign your zone — then everything in it shows up here."
        />
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {centers.map((c) => (
            <Card key={c.id} className="flex flex-col gap-2">
              <div className="flex items-start justify-between gap-2">
                <div className="display text-[17px]">{c.name}</div>
                {c.is_active ? (
                  <Badge tone="forest">active</Badge>
                ) : (
                  <Badge tone="danger">inactive</Badge>
                )}
              </div>
              <div className="text-[13px] text-ink-faint">
                {[c.state, c.country].filter(Boolean).join(", ")}
                {c.zone_name ? ` · ${c.zone_name}` : ""}
              </div>
              {c.contacts.length > 0 && (
                <div className="mt-1 border-t border-line/70 pt-2 text-[13px] text-ink-soft">
                  {c.contacts.slice(0, 2).map((ct) => (
                    <div key={ct.name} className="truncate">
                      {ct.name}
                      {ct.phone && <span className="text-ink-faint"> · {ct.phone}</span>}
                    </div>
                  ))}
                </div>
              )}
              {c.needs_followup && (
                <Badge tone="gold" title={c.followup_reasons.join(", ")}>
                  needs follow-up
                </Badge>
              )}
            </Card>
          ))}
        </div>
      )}
    </>
  );
}
