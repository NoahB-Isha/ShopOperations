/* The little inbox — admin-posted notices behind a top-bar bell. Opening it
   marks everything read; admins get a compose box and per-notice delete. */
import { useState } from "react";
import { useDeleteNotice, useMarkNoticesRead, useNotices, usePostNotice } from "../api/hooks";
import { useAuth } from "../auth/AuthContext";
import { Button, Spinner, useToast } from "../design";
import { Icons } from "../nav";
import { fmtWhen } from "../pages/shared/OpsBits";
import { useSillyLabel } from "../silly";

function Compose({ onDone }: { onDone: () => void }) {
  const post = usePostNotice();
  const toast = useToast();
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const submit = () =>
    post.mutate(
      { title: title.trim(), body: body.trim() },
      {
        onSuccess: () => {
          setTitle("");
          setBody("");
          toast.success("Notice posted — everyone sees it in their inbox.");
          onDone();
        },
        onError: (e) => toast.error(e.message),
      },
    );
  return (
    <div className="flex flex-col gap-2 border-t border-outline-variant/60 px-3 pt-3 pb-1">
      <input
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        placeholder="Post a notice — title"
        aria-label="Notice title"
        className="m3-control w-full"
      />
      <textarea
        value={body}
        onChange={(e) => setBody(e.target.value)}
        placeholder="Details (optional)"
        aria-label="Notice details"
        rows={2}
        className="m3-control w-full resize-none py-2"
      />
      <div className="flex justify-end">
        <Button size="sm" disabled={!title.trim() || post.isPending} onClick={submit}>
          {post.isPending ? <Spinner size={14} /> : "Post"}
        </Button>
      </div>
    </div>
  );
}

export function InboxMenu() {
  const { roles } = useAuth();
  const isAdmin = roles.has("admin");
  const s = useSillyLabel();
  const [open, setOpen] = useState(false);
  const inbox = useNotices();
  const markRead = useMarkNoticesRead();
  const del = useDeleteNotice();
  const toast = useToast();

  const unread = inbox.data?.unread ?? 0;
  const items = inbox.data?.items ?? [];

  const toggle = () => {
    const next = !open;
    setOpen(next);
    if (next && unread > 0) markRead.mutate();
  };

  return (
    <div className="relative">
      <button
        aria-label={unread > 0 ? `Inbox — ${unread} unread` : "Inbox"}
        title="Inbox"
        onClick={toggle}
        className="state-layer relative grid h-10 w-10 place-items-center rounded-full text-on-surface-variant"
      >
        {Icons.bell}
        {unread > 0 && (
          <span
            className="absolute top-1 right-1 grid h-4 min-w-4 place-items-center rounded-full
              bg-primary px-1 text-[10px] font-bold text-on-primary"
          >
            {unread > 9 ? "9+" : unread}
          </span>
        )}
      </button>
      {open && (
        <>
          <button
            aria-label="Close inbox"
            className="fixed inset-0 z-30 cursor-default"
            onClick={() => setOpen(false)}
          />
          <div
            className="animate-pop-in absolute right-0 z-40 mt-1 flex max-h-[70vh] w-80 flex-col
              rounded-(--radius-lg) bg-surface-container-high pt-2 pb-2 shadow-(--shadow-e2)"
          >
            <div className="px-4 pt-1 pb-2">
              <span className="title-m text-on-surface">{s("Inbox")}</span>
            </div>
            <div className="min-h-0 grow overflow-y-auto px-1.5">
              {inbox.isLoading ? (
                <div className="grid place-items-center py-8">
                  <Spinner size={18} />
                </div>
              ) : items.length === 0 ? (
                <p className="px-3 py-6 text-center text-[13px] text-on-surface-variant">
                  {s(
                    isAdmin
                      ? "Nothing here yet — post the first notice below."
                      : "Nothing here yet.",
                  )}
                </p>
              ) : (
                <ul className="flex flex-col gap-1">
                  {items.map((n) => (
                    <li
                      key={n.id}
                      className={`group rounded-(--radius-md) px-3 py-2 ${
                        n.read ? "" : "bg-secondary-container/40"
                      }`}
                    >
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0">
                          <div className="text-[13.5px] font-semibold text-on-surface">
                            {n.title}
                          </div>
                          {n.body && (
                            <div className="mt-0.5 text-[12.5px] leading-4.5 whitespace-pre-wrap text-on-surface-variant">
                              {n.body}
                            </div>
                          )}
                          <div className="mt-1 text-[11px] text-on-surface-variant">
                            {n.author || "admin"} · {fmtWhen(n.created_at)}
                          </div>
                        </div>
                        {isAdmin && (
                          <button
                            aria-label={`Delete notice ${n.title}`}
                            className="shrink-0 rounded-full p-1 text-on-surface-variant opacity-0
                              transition-opacity group-hover:opacity-100 hover:text-error"
                            onClick={() =>
                              del.mutate(n.id, {
                                onSuccess: () => toast.info("Notice deleted."),
                                onError: (e) => toast.error(e.message),
                              })
                            }
                          >
                            <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                              <path
                                d="M3 3l8 8M11 3l-8 8"
                                stroke="currentColor"
                                strokeWidth="1.6"
                                strokeLinecap="round"
                              />
                            </svg>
                          </button>
                        )}
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </div>
            {isAdmin && <Compose onDone={() => inbox.refetch()} />}
          </div>
        </>
      )}
    </div>
  );
}
