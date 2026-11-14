import { Link } from "@tanstack/react-router";
import { BookOpen, History, Info, LifeBuoy, UserRound } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

const items = [
  { to: "/history", label: "Chat History", icon: History },
  { to: "/manual", label: "Manual", icon: BookOpen },
  { to: "/about", label: "About", icon: Info },
  { to: "/help", label: "Need Help?", icon: LifeBuoy },
] as const;

export function ProfileDropdown() {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        aria-label="Open profile menu"
        className="flex size-10 items-center justify-center rounded-full border border-border bg-secondary text-foreground transition-colors hover:border-primary hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring data-[state=open]:border-primary data-[state=open]:bg-accent data-[state=open]:text-accent-foreground"
      >
        <UserRound className="size-5" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-56">
        <DropdownMenuLabel className="font-normal">
          <p className="text-sm font-semibold text-foreground">Guest user</p>
          <p className="text-xs text-muted-foreground">Conversations saved in this browser</p>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        {items.map(({ to, label, icon: Icon }) => (
          <DropdownMenuItem key={to} asChild className="cursor-pointer gap-2.5 py-2 focus:bg-accent focus:text-accent-foreground">
            <Link to={to}>
              <Icon className="size-4" />
              {label}
            </Link>
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
