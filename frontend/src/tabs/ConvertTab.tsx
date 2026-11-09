import { useState } from "react";
import { ApiError, downloadBlob, errorMessage, plural, postForm } from "../api";
import FileDrop from "../components/FileDrop";

type Status =
  | { kind: "idle" }
  | { kind: "info"; msg: string }
  | { kind: "success"; msg: string; warnings?: string[] }
  | { kind: "error"; msg: string; warnings?: string[] };

interface ConvertReport {
  filename: string;
  xml: string;
  question_count: number;
  mcq_count: number;
  essay_count: number;
  warnings: string[];
}

export default function ConvertTab() {
  const [file, setFile] = useState<File | null>(null);
  const [status, setStatus] = useState<Status>({ kind: "idle" });
  const [busy, setBusy] = useState(false);

  const convert = async () => {
    if (!file) return;
    setBusy(true);
    setStatus({ kind: "info", msg: "Converting…" });
    const form = new FormData();
    form.append("file", file);
    try {
      const report = await postForm<ConvertReport>("/api/convert/?report=1", form);
      const blob = new Blob([report.xml], { type: "application/xml;charset=utf-8" });
      const outName = report.filename || file.name.replace(/\.docx$/i, "") + "_moodle.xml";
      downloadBlob(blob, outName);
      const parts = [plural(report.mcq_count, "multiple-choice question")];
      if (report.essay_count) parts.push(plural(report.essay_count, "essay question"));
      setStatus({
        kind: "success",
        msg: `Downloaded ${outName} (${(blob.size / 1024).toFixed(1)} KB): ${parts.join(", ")}.`,
        warnings: report.warnings,
      });
    } catch (err) {
      setStatus({
        kind: "error",
        msg: errorMessage(err),
        warnings: err instanceof ApiError ? err.warnings : undefined,
      });
    } finally {
      setBusy(false);
    }
  };

  const reset = () => {
    setFile(null);
    setStatus({ kind: "idle" });
  };

  return (
    <>
      <p className="lead">
        Upload a multiple-choice <code>.docx</code> file. Mark the correct answer with an{" "}
        <code>Answer: X</code> line, a highlight, or bold. The converted Moodle XML will download
        automatically, and anything that could not be converted is listed below.
      </p>

      <FileDrop
        file={file}
        accept={[".docx"]}
        onFile={(f) => {
          setFile(f);
          setStatus({ kind: "idle" });
        }}
        onError={(msg) => {
          setFile(null);
          setStatus({ kind: "error", msg });
        }}
      />

      <div className="row">
        <button className="primary" disabled={!file || busy} onClick={convert}>
          {busy ? "Converting…" : "Convert & download"}
        </button>
        <button className="secondary" onClick={reset} disabled={busy}>
          Reset
        </button>
      </div>

      {status.kind !== "idle" && <div className={`status ${status.kind}`}>{status.msg}</div>}

      {(status.kind === "success" || status.kind === "error") &&
        status.warnings &&
        status.warnings.length > 0 && (
          <div className="warnings">
            <div className="warnings-title">
              {status.kind === "success"
                ? `${plural(status.warnings.length, "issue")} — these were skipped or need checking:`
                : "Why each question was skipped:"}
            </div>
            <ul>
              {status.warnings.map((w, i) => (
                <li key={i}>{w}</li>
              ))}
            </ul>
          </div>
        )}
    </>
  );
}
