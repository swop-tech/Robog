import { Bot } from "lucide-react";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { SAMPLE_ROBOTS } from "@/config/app";
import { cn } from "@/lib/utils";

interface RobotSelectorProps {
  value: string | null;
  onChange: (robotId: string) => void;
  className?: string;
}

export function RobotSelector({ value, onChange, className }: RobotSelectorProps) {
  return (
    <Select value={value ?? ""} onValueChange={onChange}>
      <SelectTrigger
        aria-label="Machine to enquire about"
        className={cn(
          "h-10 w-full gap-2 rounded-lg bg-background shadow-none focus:ring-2 focus:ring-ring data-[placeholder]:text-muted-foreground",
          value && "border-primary/60",
          className,
        )}
      >
        <Bot className={cn("size-4 shrink-0", value ? "text-primary" : "text-muted-foreground")} />
        <SelectValue placeholder="Select a machine to enquire about" />
      </SelectTrigger>
      <SelectContent>
        <SelectGroup>
          <SelectLabel className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
            Sample machines
          </SelectLabel>
          {SAMPLE_ROBOTS.map((r) => (
            <SelectItem key={r.id} value={r.id} className="focus:bg-accent focus:text-accent-foreground">
              <span className="font-medium">{r.name}</span>
              <span className="ml-2 text-xs text-muted-foreground">{r.type}</span>
            </SelectItem>
          ))}
        </SelectGroup>
      </SelectContent>
    </Select>
  );
}
