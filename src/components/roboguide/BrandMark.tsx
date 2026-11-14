import { Link } from "@tanstack/react-router";
import logo from "@/assets/roboguide-logo-sm.png";
import { APP_NAME } from "@/config/app";
import { cn } from "@/lib/utils";

export function BrandLogo({ className }: { className?: string }) {
  return <img src={logo} alt="" width={160} height={160} className={cn("size-9 object-contain", className)} />;
}

export function BrandMark({ compact = false }: { compact?: boolean }) {
  return (
    <Link to="/" className="flex items-center gap-2.5 rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
      <BrandLogo />
      <span className={cn("font-display text-xl font-bold leading-none tracking-tight text-foreground", compact && "hidden sm:inline")}>
        RoboGuide <span className="text-primary">AI</span>
        <span className="sr-only">{APP_NAME} home</span>
      </span>
    </Link>
  );
}
