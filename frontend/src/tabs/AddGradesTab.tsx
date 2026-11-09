import { useEffect, useRef, useState } from "react";
import { errorMessage, payloadToBlob, plural, postForm, TaskResult } from "../api";
import FileDrop from "../components/FileDrop";
import TaskResultView, { downloadTaskResult } from "../components/TaskResultView";

interface ClassListInfo {
  sections: number;
  students: number;
  columns: { name: string; gradable: boolean }[];
}
interface ResultsInfo {
  columns: string[];
  grade_column: number | null;
  out_of: number | null;
  students: number;
  multi_attempt_students: number;
  matched?: number;
}
interface InspectResponse {
  classlist?: ClassListInfo;
  results?: ResultsInfo;
}

const NEW = "__new__";
const ROUND_STEPS = [
  { value: "0.01", label: "0.01 (2 decimals)" },
  { value: "0.1", label: "0.1" },
  { value: "0.25", label: "0.25 (quarter)" },
  { value: "0.5", label: "0.5 (half)" },
  { value: "1", label: "1 (whole number)" },
];

function roundTo(v: number, step: number) {
  return Math.round(v / step + 1e-9) * step;
}

export default function AddGradesTab() {
  const [classlist, setClasslist] = useState<File | null>(null);
  const [results, setResults] = useState<File | null>(null);
  const [info, setInfo] = useState<InspectResponse>({});
  const [inspecting, setInspecting] = useState(false);

  const [gradeCol, setGradeCol] = useState<string>("");
  const [outOf, setOutOf] = useState("");
  const [rescale, setRescale] = useState(false);
  const [scaleTo, setScaleTo] = useState("7");
  const [roundStep, setRoundStep] = useState("0.01");
  const [target, setTarget] = useState("");
  const [newName, setNewName] = useState("");
  const [attempts, setAttempts] = useState("highest");

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<TaskResult | null>(null);
  const [updatedClasslist, setUpdatedClasslist] = useState(false);
  const inspectSeq = useRef(0);

  // Read column lists / counts whenever a file changes.
  useEffect(() => {
    if (!classlist && !results) {
      setInfo({});
      return;
    }
    const seq = ++inspectSeq.current;
    const form = new FormData();
    if (classlist) form.append("classlist", classlist);
    if (results) form.append("results", results);
    setInspecting(true);
    postForm<InspectResponse>("/api/grades/inspect/", form)
      .then((data) => {
        if (seq !== inspectSeq.current) return;
        setInfo(data);
        setError(null);
        if (data.results) {
          setGradeCol(data.results.grade_column != null ? String(data.results.grade_column) : "");
          setOutOf(data.results.out_of != null ? String(data.results.out_of) : "");
        }
        if (data.classlist) {
          setTarget((t) => {
            const names = data.classlist!.columns.filter((c) => c.gradable).map((c) => c.name);
            if (t === NEW || names.includes(t)) return t;
            return names[0] ?? NEW;
          });
        }
      })
      .catch((err) => {
        if (seq !== inspectSeq.current) return;
        setInfo({});
        setError(errorMessage(err));
      })
      .finally(() => seq === inspectSeq.current && setInspecting(false));
  }, [classlist, results]);

  const cl = info.classlist;
  const rs = info.results;
  const gradable = cl?.columns.filter((c) => c.gradable).map((c) => c.name) ?? [];
  const isNew = target === NEW;
  const columnName = isNew ? newName.trim() : target;
  const outOfNum = parseFloat(outOf);
  const scaleNum = parseFloat(scaleTo);
  const stepNum = parseFloat(roundStep);

  const problems: string[] = [];
  if (!classlist) problems.push("Choose the class list.");
  if (!results) problems.push("Choose the quiz results file.");
  if (classlist && results && !inspecting) {
    if (gradeCol === "") problems.push("Choose the grade column.");
    if (!columnName) problems.push(isNew ? "Enter a name for the new column." : "Choose a column.");
    if (isNew && cl?.columns.some((c) => c.name.toLowerCase() === columnName.toLowerCase()))
      problems.push(`The class list already has a “${columnName}” column — pick it from the list.`);
    if (rescale && !(outOfNum > 0)) problems.push("Enter what the grade is out of.");
    if (rescale && !(scaleNum > 0)) problems.push("Enter the new maximum.");
  }

  const example =
    rescale && outOfNum > 0 && scaleNum > 0
      ? (() => {
          const sample = outOfNum * 0.95;
          return `e.g. ${+sample.toFixed(2)} / ${outOfNum} → ${+roundTo((sample / outOfNum) * scaleNum, stepNum).toFixed(2)} / ${scaleNum}`;
        })()
      : null;

  const run = async () => {
    if (!classlist || !results || problems.length) return;
    setBusy(true);
    setError(null);
    setResult(null);
    const form = new FormData();
    form.append("classlist", classlist);
    form.append("results", results);
    form.append("grade_column", gradeCol);
    form.append("out_of", outOf);
    form.append("scale_to", rescale ? scaleTo : "");
    form.append("round_step", roundStep);
    form.append("target_column", columnName);
    form.append("new_column", isNew ? "1" : "0");
    form.append("attempts", attempts);
    try {
      const res = await postForm<TaskResult>("/api/grades/add-to-classlist/", form);
      setResult(res);
      downloadTaskResult(res);
      // Continue from the updated class list, so the next quiz builds on it.
      setClasslist(new File([payloadToBlob(res.file)], res.file.name, { type: res.file.type }));
      setTarget(columnName);
      setNewName("");
      setUpdatedClasslist(true);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <p className="lead">
        Put the grades from a Moodle quiz export into a column of the class list. Students are
        matched by ID and e-mail (<code>2021/11262</code> ↔ <code>…2111262@miuegypt.edu.eg</code>).
        Class-list students with no quiz result get <b>0</b>. You get the updated class list and a
        task report.
      </p>

      <div className="grid2">
        <FileDrop
          label="1. Class list"
          file={classlist}
          accept={[".xlsx"]}
          compact
          onFile={(f) => { setClasslist(f); setResult(null); setUpdatedClasslist(false); }}
          onError={setError}
          info={
            cl && (
              <>
                {plural(cl.students, "student")} in {plural(cl.sections, "section")}
                {updatedClasslist && " · now using the updated file from the last run"}
              </>
            )
          }
        />
        <FileDrop
          label="2. Quiz results"
          file={results}
          accept={[".csv", ".xlsx", ".ods"]}
          compact
          onFile={(f) => { setResults(f); setResult(null); }}
          onError={setError}
          info={
            rs && (
              <>
                {plural(rs.students, "student")}
                {rs.multi_attempt_students > 0 &&
                  `, ${rs.multi_attempt_students} with several attempts`}
                {rs.matched != null && (
                  <>
                    {" · "}
                    <b>{rs.matched}</b> matched with the class list
                  </>
                )}
              </>
            )
          }
        />
      </div>

      {inspecting && <div className="status info">Reading files…</div>}

      {rs && cl && !inspecting && (
        <div className="form">
          <div className="form-row">
            <label htmlFor="ag-grade">Grade column</label>
            <select id="ag-grade" value={gradeCol} onChange={(e) => setGradeCol(e.target.value)}>
              <option value="" disabled>Choose…</option>
              {rs.columns.map((c, i) => (
                <option key={i} value={i}>{c}</option>
              ))}
            </select>
          </div>

          <div className="form-row">
            <label htmlFor="ag-outof">Grade is out of</label>
            <input id="ag-outof" type="number" min="0" step="any" value={outOf}
                   onChange={(e) => setOutOf(e.target.value)} />
          </div>

          <div className="form-row">
            <label className="check">
              <input type="checkbox" checked={rescale} onChange={(e) => setRescale(e.target.checked)} />
              Rescale to
            </label>
            <div className="inline">
              <input type="number" min="0" step="any" value={scaleTo} disabled={!rescale}
                     aria-label="New maximum" onChange={(e) => setScaleTo(e.target.value)} />
              {example && <span className="muted">{example}</span>}
            </div>
          </div>

          <div className="form-row">
            <label htmlFor="ag-round">Round to</label>
            <select id="ag-round" value={roundStep} onChange={(e) => setRoundStep(e.target.value)}>
              {ROUND_STEPS.map((s) => (
                <option key={s.value} value={s.value}>{s.label}</option>
              ))}
            </select>
          </div>

          <div className="form-row">
            <label htmlFor="ag-attempts">Several attempts</label>
            <select id="ag-attempts" value={attempts} onChange={(e) => setAttempts(e.target.value)}>
              <option value="highest">Use the highest grade</option>
              <option value="latest">Use the latest attempt</option>
              <option value="first">Use the first attempt</option>
            </select>
          </div>

          <div className="form-row">
            <label htmlFor="ag-target">Put grades in column</label>
            <div className="inline">
              <select id="ag-target" value={target} onChange={(e) => setTarget(e.target.value)}>
                {gradable.map((c) => (
                  <option key={c} value={c}>{c}</option>
                ))}
                <option value={NEW}>➕ Add a new column…</option>
              </select>
              {isNew && (
                <input type="text" placeholder="New column name, e.g. Quiz 2" value={newName}
                       aria-label="New column name" onChange={(e) => setNewName(e.target.value)} />
              )}
            </div>
          </div>
          {!isNew && target && (
            <div className="muted note">
              Existing values in “{target}” will be replaced for every student.
            </div>
          )}
        </div>
      )}

      {problems.length > 0 && classlist && results && !inspecting && (
        <ul className="problems">
          {problems.map((p) => <li key={p}>{p}</li>)}
        </ul>
      )}

      <div className="row">
        <button className="primary" disabled={busy || inspecting || problems.length > 0} onClick={run}>
          {busy ? "Working…" : "Add grades & download"}
        </button>
      </div>

      {error && <div className="status error">{error}</div>}
      {result && <TaskResultView result={result} />}
    </>
  );
}
