/* First-login personalization: your name, a fun avatar, a color. Appears
   once — the backend stamps profile_setup_at on ANY save, including "Maybe
   later", so skipping is remembered too. Settings reopens the same dialog
   any time via `open`.

   Decoration rules apply: the avatar is presentation only, and a person who
   skips keeps their initial-on-primary disc. */
import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { UserOut } from "../api/types";
import { AVATAR_COLORS, AVATAR_ICONS, DEFAULT_AVATAR_COLOR } from "../avatars";
import { AvatarDisc, Button, Dialog, Field, Input, Spinner, useToast } from "../design";
import { playChime } from "../sound";
import { useAuth } from "./AuthContext";

export function ProfileSetupDialog({
  open,
  onClose,
  firstRun = false,
}: {
  open: boolean;
  onClose: () => void;
  /** first sign-in: friendlier copy, and "Maybe later" records the skip */
  firstRun?: boolean;
}) {
  const { user, updateUser } = useAuth();
  const toast = useToast();
  const [name, setName] = useState("");
  const [icon, setIcon] = useState("");
  const [color, setColor] = useState(DEFAULT_AVATAR_COLOR);
  const [busy, setBusy] = useState(false);

  // re-seed from the signed-in user each time the dialog opens
  useEffect(() => {
    if (!open || !user) return;
    setName(user.display_name || "");
    setIcon(user.avatar_icon || "");
    setColor(user.avatar_color || DEFAULT_AVATAR_COLOR);
  }, [open, user]);

  if (!user) return null;

  const save = async (body: { display_name?: string; avatar_icon?: string; avatar_color?: string }) => {
    setBusy(true);
    try {
      const fresh = await api<UserOut>("/auth/me", { method: "PATCH", body });
      updateUser(fresh);
      onClose();
      return true;
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Couldn't save — try again.");
      return false;
    } finally {
      setBusy(false);
    }
  };

  const submit = async () => {
    if (!name.trim()) {
      toast.error("What should we call you? The name can't be empty.");
      return;
    }
    const ok = await save({
      display_name: name.trim(),
      avatar_icon: icon,
      avatar_color: icon ? color : "",
    });
    if (ok) {
      playChime();
      toast.success(firstRun ? "Welcome aboard! 🎉" : "Profile updated.");
    }
  };

  return (
    <Dialog
      open={open}
      onClose={firstRun ? () => void save({}) : onClose}
      title={firstRun ? "Customize" : "Edit profile"}
      footer={
        <div className="flex w-full items-center justify-between gap-2">
          {firstRun ? (
            <Button variant="ghost" size="sm" disabled={busy} onClick={() => void save({})}>
              Maybe later
            </Button>
          ) : (
            <Button variant="ghost" size="sm" disabled={busy} onClick={onClose}>
              Cancel
            </Button>
          )}
          <Button disabled={busy} onClick={() => void submit()}>
            {busy ? <Spinner size={16} /> : firstRun ? "That's me" : "Save"}
          </Button>
        </div>
      }
    >
      <div className="flex flex-col gap-4">
        {firstRun && (
          <p className="-mt-1 text-[13.5px] leading-5 text-on-surface-variant">
            Pick a name your team will see, and an avatar to wear around the app. You can
            change both any time in Settings.
          </p>
        )}

        <div className="flex items-center gap-3">
          <AvatarDisc icon={icon} color={icon ? color : ""} name={name} size={64} />
          <Field label="Your name" className="grow">
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={160}
              autoComplete="name"
              aria-label="Your name"
            />
          </Field>
        </div>

        <div>
          <div className="label-caps mb-2">Pick an avatar</div>
          <div className="grid grid-cols-4 gap-2">
            {AVATAR_ICONS.map((a) => (
              <button
                key={a.id}
                type="button"
                onClick={() => setIcon(icon === a.id ? "" : a.id)}
                aria-pressed={icon === a.id}
                aria-label={a.label}
                title={a.label}
                className={`state-layer grid place-items-center rounded-(--radius-lg) p-1.5
                  transition-all ${
                    icon === a.id
                      ? "bg-secondary-container ring-2 ring-primary"
                      : "hover:bg-on-surface/8"
                  }`}
              >
                <AvatarDisc icon={a.id} color={color} size={44} />
              </button>
            ))}
          </div>
        </div>

        <div>
          <div className="label-caps mb-2">Pick a color</div>
          <div className="flex flex-wrap gap-2.5">
            {AVATAR_COLORS.map((c) => (
              <button
                key={c}
                type="button"
                onClick={() => setColor(c)}
                aria-pressed={color === c}
                aria-label={`Color ${c}`}
                style={{ backgroundColor: c }}
                className={`h-9 w-9 rounded-full transition-transform ${
                  color === c
                    ? "scale-110 ring-2 ring-primary ring-offset-2 ring-offset-surface"
                    : "hover:scale-105"
                }`}
              />
            ))}
          </div>
        </div>
      </div>
    </Dialog>
  );
}
