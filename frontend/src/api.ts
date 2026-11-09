export class ApiError extends Error {
  warnings?: string[];
  constructor(message: string, warnings?: string[]) {
    super(message);
    this.warnings = warnings;
  }
}

/** POST a multipart form and return the JSON body, throwing ApiError on failure. */
export async function postForm<T>(url: string, form: FormData): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, { method: "POST", body: form });
  } catch {
    throw new ApiError("Network error — is the backend running?");
  }
  let body: any = null;
  try {
    body = await res.json();
  } catch {
    /* non-JSON */
  }
  if (!res.ok) {
    throw new ApiError(
      body?.error || `Server returned ${res.status}`,
      Array.isArray(body?.warnings) ? body.warnings : undefined
    );
  }
  return body as T;
}

export function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

export function downloadBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export interface FilePayload {
  name: string;
  type: string;
  data: string; // base64
}

export function payloadToBlob(p: FilePayload): Blob {
  const bin = atob(p.data);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new Blob([bytes], { type: p.type });
}

/** Result of /api/grades/add-to-classlist/ and /api/grades/announce/. */
export interface TaskResult {
  file: FilePayload;
  report: FilePayload;
  totals: [string, number][];
  steps: [string, string, "ok" | "warn" | "error"][];
}

export function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}
