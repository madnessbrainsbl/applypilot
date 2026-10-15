import React, { ForwardRefExoticComponent, RefAttributes } from "react";
import Link from "next/link";

import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { LucideProps } from "lucide-react";
import { cn } from "@/lib/utils";

interface NavLinkProps {
  label: string;
  Icon: ForwardRefExoticComponent<
    Omit<LucideProps, "ref"> & RefAttributes<SVGSVGElement>
  >;
  route: string;
  pathname: string;
  expanded: boolean;
}

function NavLink({ label, Icon, route, pathname, expanded }: NavLinkProps) {
  // "/dashboard" is a prefix of every other route, so it only matches exactly.
  const isActive =
    pathname === route ||
    (route !== "/dashboard" && pathname.startsWith(`${route}/`));

  const link = (
    <Link
      href={route}
      aria-current={isActive ? "page" : undefined}
      className={cn(
        "navlink h-11 w-full rounded-lg hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        isActive ? "text-primary font-semibold" : "text-muted-foreground",
        isActive && expanded && "rounded-md bg-accent"
      )}
    >
      {isActive && (
        <span
          aria-hidden
          className="absolute left-0 top-0 h-full w-0.5 bg-primary"
        />
      )}
      {/* Fixed-width lead box (= collapsed rail width) so the icon sits at the
          same spot in both states and never moves during the slide. */}
      <span className="flex h-full w-14 shrink-0 items-center justify-center">
        <Icon className="h-6 w-6 shrink-0" strokeWidth={1.5} />
      </span>
      <span
        className={cn(
          "truncate text-sm transition-opacity duration-200",
          expanded ? "opacity-100 delay-100" : "opacity-0"
        )}
      >
        {label}
      </span>
    </Link>
  );

  // Always render the Tooltip wrapper so the <Link> keeps a stable position in
  // the tree across expand/collapse — remounting it would reset the CSS
  // transitions and make the toggle snap instead of animate.
  return (
    <Tooltip>
      <TooltipTrigger asChild>{link}</TooltipTrigger>
      {!expanded && <TooltipContent side="right">{label}</TooltipContent>}
    </Tooltip>
  );
}

export default NavLink;
