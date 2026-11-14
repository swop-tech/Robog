import { Languages } from "lucide-react";
import { Select, SelectContent, SelectItem, SelectTrigger } from "@/components/ui/select";
import { RESPONSE_LANGUAGES } from "@/config/app";

interface LanguageSelectorProps {
  value: string;
  onChange: (language: string) => void;
}

export function LanguageSelector({ value, onChange }: LanguageSelectorProps) {
  const current = RESPONSE_LANGUAGES.find((l) => l.value === value) ?? RESPONSE_LANGUAGES[0];
  return (
    <Select value={current.value} onValueChange={onChange}>
      <SelectTrigger
        aria-label={`Answer language: ${current.label}`}
        title="Answer language"
        className="h-10 w-auto gap-1.5 rounded-lg bg-background px-2.5 shadow-none focus:ring-2 focus:ring-ring"
      >
        <Languages className="size-4 text-muted-foreground" />
        <span className="font-mono text-xs font-medium">{current.code}</span>
      </SelectTrigger>
      <SelectContent align="end">
        {RESPONSE_LANGUAGES.map((l) => (
          <SelectItem
            key={l.value}
            value={l.value}
            className="focus:bg-accent focus:text-accent-foreground"
          >
            {l.label}
            <span className="ml-2 font-mono text-xs text-muted-foreground">{l.code}</span>
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
