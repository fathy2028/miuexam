import { useCallback, useRef, useState } from "react";
import "./App.css";

type Status =
  | { kind: "idle" }
  | { kind: "info"; msg: string }
  | { kind: "success"; msg: string }
  | { kind: "error"; msg: string };

function extractFilename(header: string | null, fallback: string): string {
  if (!header) return fallback;
  const m = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(header);
  return m ? decodeURIComponent(m[1]) : fallback;
}

export default function App() {
  const [file, setFile] = useState<File | null>(null);
  const [status, setStatus] = useState<Status>({ kind: "idle" });
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const pickFile = (f: File | undefined | null) => {
    if (!f) return;
    if (!f.name.toLowerCase().endsWith(".docx")) {
      setStatus({ kind: "error", msg: "Please select a .docx file." });
      setFile(null);
      return;
    }
    setFile(f);
    setStatus({ kind: "idle" });
  };

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    pickFile(e.dataTransfer.files?.[0]);
  }, []);

  const convert = async () => {
    if (!file) return;
    setBusy(true);
    setStatus({ kind: "info", msg: "Converting…" });

    const form = new FormData();
    form.append("file", file);

    try {
      const res = await fetch("/api/convert/", { method: "POST", body: form });

      if (!res.ok) {
        let msg = `Server returned ${res.status}`;
        try {
          const body = await res.json();
          if (body?.error) msg = body.error;
        } catch {
          /* non-JSON error */
        }
        setStatus({ kind: "error", msg });
        return;
      }

      const blob = await res.blob();
      const fallback = file.name.replace(/\.docx$/i, "") + "_moodle.xml";
      const outName = extractFilename(
        res.headers.get("Content-Disposition"),
        fallback
      );

      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = outName;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);

      setStatus({
        kind: "success",
        msg: `Downloaded ${outName} (${(blob.size / 1024).toFixed(1)} KB).`,
      });
    } catch (err) {
      const msg =
        err instanceof Error ? err.message : "Network error — is the backend running?";
      setStatus({ kind: "error", msg });
    } finally {
      setBusy(false);
    }
  };

  const reset = () => {
    setFile(null);
    setStatus({ kind: "idle" });
    if (inputRef.current) inputRef.current.value = "";
  };

  return (
    <div className="app">
      <div className="card">
        <h1>DOCX → Moodle XML</h1>
        <p className="lead">
          Upload a multiple-choice <code>.docx</code> file. The correct answer
          can be highlighted, bold, or marked <code>Answer: X</code>. The
          converted Moodle XML will download automatically.
        </p>

        <div
          className={"dropzone" + (dragging ? " dragging" : "")}
          onClick={() => inputRef.current?.click()}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
        >
          <input
            ref={inputRef}
            type="file"
            accept=".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            style={{ display: "none" }}
            onChange={(e) => pickFile(e.target.files?.[0])}
          />
          {file ? (
            <>
              <div className="picked">{file.name}</div>
              <div className="hint">{(file.size / 1024).toFixed(1)} KB — click or drop to replace</div>
            </>
          ) : (
            <>
              <div className="picked">Click to choose a file or drop it here</div>
              <div className="hint">.docx only</div>
            </>
          )}
        </div>

        <div className="row">
          <button
            className="primary"
            disabled={!file || busy}
            onClick={convert}
          >
            {busy ? "Converting…" : "Convert & download"}
          </button>
          <button className="secondary" onClick={reset} disabled={busy && !file}>
            Reset
          </button>
        </div>

        {status.kind !== "idle" && (
          <div className={`status ${status.kind}`}>{status.msg}</div>
        )}
      </div>
    </div>
  );
}
