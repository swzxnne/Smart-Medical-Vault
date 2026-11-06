export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function formatDate(iso: string): string {
  // Date-only values ("2024-03-12") are calendar dates; don't let UTC parsing shift them a day.
  const date = /^\d{4}-\d{2}-\d{2}$/.test(iso) ? new Date(`${iso}T00:00:00`) : new Date(iso);
  return date.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

export function formatDocType(docType: string | null): string {
  if (!docType) return "Document";
  const label = docType.replace(/_/g, " ");
  return label.charAt(0).toUpperCase() + label.slice(1);
}

export function ageFrom(dob: string | null): number | null {
  if (!dob) return null;
  // Parse the parts directly: `new Date("YYYY-MM-DD")` is UTC and can shift a day locally.
  const [year, month, day] = dob.split("-").map(Number);
  const now = new Date();
  let age = now.getFullYear() - year;
  const beforeBirthday =
    now.getMonth() + 1 < month || (now.getMonth() + 1 === month && now.getDate() < day);
  if (beforeBirthday) age -= 1;
  return age;
}

/** Today's date as YYYY-MM-DD in the user's timezone (toISOString() would use UTC). */
export function todayLocal(): string {
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}
