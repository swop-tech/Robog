import type { ReactNode } from "react";
import { BrandMark } from "./BrandMark";
import { ProfileDropdown } from "./ProfileDropdown";

interface HeaderProps {
  /** Left-most slot, e.g. mobile menu button. */
  leading?: ReactNode;
  /** Centre slot (robot selector on the chat screen). */
  center?: ReactNode;
  /** Extra controls before the profile icon. */
  actions?: ReactNode;
  /** Shown on its own row below the header on small screens. */
  mobileRow?: ReactNode;
}

export function Header({ leading, center, actions, mobileRow }: HeaderProps) {
  return (
    <header className="shrink-0 border-b border-border bg-background">
      <div className="flex h-16 items-center gap-3 px-4 sm:px-6">
        {leading}
        <BrandMark compact={!!center} />
        <div className="hidden flex-1 justify-center md:flex">{center && <div className="w-full max-w-sm">{center}</div>}</div>
        <div className="ml-auto flex items-center gap-2 md:ml-0">
          {actions}
          <ProfileDropdown />
        </div>
      </div>
      {mobileRow && <div className="border-t border-border px-4 py-2 md:hidden">{mobileRow}</div>}
    </header>
  );
}
