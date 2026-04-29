import { cn } from "@/lib/utils";

interface CardProps {
  title?: string;
  children: React.ReactNode;
  className?: string;
  /** When true, renders the hover-elevation shadow + cursor pointer.
   *  Use for cards that the whole tile is clickable. */
  interactive?: boolean;
}

export function Card({ title, children, className, interactive }: CardProps) {
  return (
    <div
      className={cn(
        // Surface + 1px hairline ring + multi-layer soft shadow gives
        // the card depth on a tinted page background. The shadow is
        // closer in feel to Linear / Vercel than Tailwind's default
        // shadow-sm, which on white-on-white pages was effectively
        // invisible.
        "rounded-xl bg-surface p-6 ring-1 ring-line shadow-card transition-all",
        interactive && "cursor-pointer hover:shadow-card-hover hover:ring-line/80",
        className,
      )}
    >
      {title && (
        <h3 className="mb-4 text-base font-semibold tracking-tight text-fg">
          {title}
        </h3>
      )}
      {children}
    </div>
  );
}
