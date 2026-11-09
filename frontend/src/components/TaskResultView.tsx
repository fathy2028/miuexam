import { downloadBlob, payloadToBlob, TaskResult } from "../api";

/** Summary of a finished gradebook task, with buttons to (re)download both files. */
export default function TaskResultView({ result }: { result: TaskResult }) {
  const problems = result.steps.filter((s) => s[2] !== "ok").length;
  return (
    <div className="result">
      <div className={`status ${problems ? "warning" : "success"}`}>
        Done — {result.file.name} and the task report were downloaded.
        {problems > 0 && " Some students need checking; see the task report."}
      </div>

      <table className="totals">
        <tbody>
          {result.totals.map(([label, n]) => (
            <tr key={label}>
              <td>{label}</td>
              <td className="num">{n}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <ol className="steps">
        {result.steps.map(([name, detail, status], i) => (
          <li key={i} className={status}>
            <span className="step-name">{name}</span>
            <span className="step-detail">{detail}</span>
          </li>
        ))}
      </ol>

      <div className="row">
        <button className="secondary" onClick={() => downloadBlob(payloadToBlob(result.file), result.file.name)}>
          Download {result.file.name}
        </button>
        <button className="secondary" onClick={() => downloadBlob(payloadToBlob(result.report), result.report.name)}>
          Download task report
        </button>
      </div>
    </div>
  );
}

export function downloadTaskResult(result: TaskResult) {
  downloadBlob(payloadToBlob(result.file), result.file.name);
  // A short gap keeps browsers from collapsing the two downloads.
  setTimeout(() => downloadBlob(payloadToBlob(result.report), result.report.name), 400);
}
