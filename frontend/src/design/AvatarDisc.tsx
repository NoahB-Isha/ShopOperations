/* The profile avatar: the person's chosen art on their chosen color. Falls
   back to their initial on the primary color until they've picked (or if an
   icon id ever stops existing — the backend stores strings, this renders
   honestly whatever it finds). */
import { DEFAULT_AVATAR_COLOR, avatarArt, glyphColorFor } from "../avatars";

export function AvatarDisc({
  icon,
  color,
  name,
  size = 32,
  className = "",
}: {
  icon?: string;
  color?: string;
  name?: string;
  size?: number;
  className?: string;
}) {
  const art = icon ? avatarArt(icon) : undefined;
  const disc = color || (art ? DEFAULT_AVATAR_COLOR : "");
  const initial = (name || "?").trim().slice(0, 1).toUpperCase() || "?";
  return (
    <span
      aria-hidden
      className={`grid shrink-0 select-none place-items-center overflow-hidden rounded-full ${
        disc ? "" : "bg-tertiary-container text-on-tertiary-container"
      } ${className}`}
      style={{
        width: size,
        height: size,
        ...(disc ? { backgroundColor: disc, color: glyphColorFor(disc) } : {}),
      }}
    >
      {art ? (
        <svg
          viewBox={art.viewBox}
          width={Math.round(size * (art.stroke ? 0.62 : 0.72))}
          height={Math.round(size * (art.stroke ? 0.62 : 0.72))}
          // Lucide art is stroke-drawn; the devi figure is filled
          fill={art.stroke ? "none" : "currentColor"}
          stroke={art.stroke ? "currentColor" : "none"}
          strokeWidth={art.stroke ? 2 : 0}
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          {art.node}
        </svg>
      ) : (
        <span style={{ fontSize: size * 0.45 }} className="font-semibold leading-none">
          {initial}
        </span>
      )}
    </span>
  );
}
