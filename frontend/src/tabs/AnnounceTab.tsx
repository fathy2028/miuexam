import { useEffect, useRef, useState } from "react";
import { errorMessage, plural, postForm, TaskResult } from "../api";
import FileDrop from "../components/FileDrop";
import TaskResultView, { downloadTaskResult } from "../components/TaskResultView";

interface ClassListInfo {
  sections: number;
  students: number;
  columns: { name: string; gradable: boolean }[];
}
interface TemplateInfo {
  columns: { name: string; numeric: boolean; key: boolean }[];
  key_column: string;
  students: number;
  matched?: number;
}
interface InspectResponse {
  classlist?: ClassListInfo;
  template?: TemplateInfo;
}

/** target "" = add as a new column in the report */
type Mapping = { checked: boolean; target: string };

/** Total candidates are identified as "t:<template column>" or "n:<class list column>". */
interface Candidate {
  id: string;
  label: string;
  hint?: string;
}

const DEFAULT_TOTAL_NAME = "Total Course Work";

export default function AnnounceTab() {
  const [classlist, setClasslist] = useState<File | null>(null);
  const [template, setTemplate] = useState<File | null>(null);
  const [info, setInfo] = useState<InspectResponse>({});
  const [inspecting, setInspecting] = useState(false);

  const [mappings, setMappings] = useState<Record<string, Mapping>>({});
  const [totalOn, setTotalOn] = useState(false);
  const [totalName, setTotalName] = useState(DEFAULT_TOTAL_NAME);
  const [totalSel, setTotalSel] = useState<string[]>([]);
  const [draftSel, setDraftSel] = useState<string[]>([]);
  const dialogRef = useRef<HTMLDialogElement>(null);

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<TaskResult | null>(null);
  const inspectSeq = useRef(0);

  useEffect(() => {
    if (!classlist && !template) {
      setInfo({});
      return;
    }
    const seq = ++inspectSeq.current;
    const form = new FormData();
    if (classlist) form.append("classlist", classlist);
    if (template) form.append("template", template);
    setInspecting(true);
    postForm<InspectResponse>("/api/grades/inspect/", form)
      .then((data) => {
        if (seq !== inspectSeq.current) return;
        setInfo(data);
        setError(null);
      })
      .catch((err) => {
        if (seq !== inspectSeq.current) return;
        setInfo({});
        setError(errorMessage(err));
      })
      .finally(() => seq === inspectSeq.current && setInspecting(false));
  }, [classlist, template]);

  const cl = info.classlist;
  const tp = info.template;
  const sources = cl?.columns.filter((c) => c.gradable).map((c) => c.name) ?? [];
  const targets = tp?.columns.filter((c) => !c.key).map((c) => c.name) ?? [];

  // (Re)initialise mappings when the column lists change; keep user choices.
  useEffect(() => {
    if (!cl || !tp) return;
    setMappings((prev) => {
      const next: Record<string, Mapping> = {};
      for (const s of sources) {
        const p = prev[s];
        const same = targets.find((t) => t.toLowerCase() === s.toLowerCase()) ?? "";
        next[s] = p && (p.target === "" || targets.includes(p.target)) ? p : { checked: false, target: same };
      }
      return next;
    });
  }, [cl, tp]);

  const checked = sources.filter((s) => mappings[s]?.checked);

  const candidates: Candidate[] = [];
  if (tp) {
    for (const c of tp.columns) {
      if (c.key) continue;
      const by = checked.find((s) => mappings[s].target === c.name);
      if (!c.numeric && !by) continue;
      candidates.push({ id: `t:${c.name}`, label: c.name, hint: by ? `← ${by} from class list` : undefined });
    }
  }
  for (const s of checked) {
    if (mappings[s].target === "") candidates.push({ id: `n:${s}`, label: s, hint: "new column" });
  }
  const validIds = new Set(candidates.map((c) => c.id));
  const effectiveSel = totalSel.filter((id) => validIds.has(id));

  const usedTargets = checked.map((s) => mappings[s].target).filter(Boolean);
  const problems: string[] = [];
  if (!classlist) problems.push("Choose the class list.");
  if (!template) problems.push("Choose the grade report template.");
  if (cl && tp && !inspecting) {
    if (!checked.length && !totalOn) problems.push("Tick at least one class list column.");
    if (new Set(usedTargets).size !== usedTargets.length)
      problems.push("Two class list columns override the same report column.");
    if (totalOn && !effectiveSel.length) problems.push("Choose the columns for the total.");
    if (totalOn && !totalName.trim()) problems.push("Enter a name for the total column.");
  }

  const setMapping = (s: string, patch: Partial<Mapping>) =>
    setMappings((m) => ({ ...m, [s]: { ...m[s], ...patch } }));

  const openTotalDialog = () => {
    // Default: everything coming from the class list.
    setDraftSel(
      effectiveSel.length
        ? effectiveSel
        : checked.map((s) => (mappings[s].target ? `t:${mappings[s].target}` : `n:${s}`))
    );
    dialogRef.current?.showModal();
  };

  const closeDialog = (save: boolean) => {
    if (save) setTotalSel(draftSel);
    else if (!effectiveSel.length) setTotalOn(false);
    dialogRef.current?.close();
  };

  const run = async () => {
    if (!classlist || !template || problems.length) return;
    setBusy(true);
    setError(null);
    setResult(null);
    const config = {
      mappings: checked.map((s) => ({ source: s, target: mappings[s].target || null })),
      total: {
        enabled: totalOn,
        name: totalName.trim(),
        columns: effectiveSel.map((id) =>
          id.startsWith("t:") ? { kind: "template", name: id.slice(2) } : { kind: "new", source: id.slice(2) }
        ),
      },
    };
    const form = new FormData();
    form.append("classlist", classlist);
    form.append("template", template);
    form.append("config", JSON.stringify(config));
    try {
      const res = await postForm<TaskResult>("/api/grades/announce/", form);
      setResult(res);
      downloadTaskResult(res);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <p className="lead">
        Copy class-list columns into the Moodle grade report so students can see them. Tick a
        column and choose which report column it overrides, or leave it as a new column. Report
        students missing from the class list get <b>0</b>. You get the updated report as a{" "}
        <code>.csv</code> file, ready for Moodle's grade import, and a task report.
      </p>

      <div className="grid2">
        <FileDrop
          label="1. Class list"
          file={classlist}
          accept={[".xlsx"]}
          compact
          onFile={(f) => { setClasslist(f); setResult(null); }}
          onError={setError}
          info={cl && `${plural(cl.students, "student")} in ${plural(cl.sections, "section")}`}
        />
        <FileDrop
          label="2. Grade report template"
          file={template}
          accept={[".ods", ".csv", ".xlsx"]}
          compact
          onFile={(f) => { setTemplate(f); setResult(null); }}
          onError={setError}
          info={
            tp && (
              <>
                {plural(tp.students, "student")} (by {tp.key_column})
                {tp.matched != null && (
                  <>
                    {" · "}
                    <b>{tp.matched}</b> matched with the class list
                  </>
                )}
              </>
            )
          }
        />
      </div>

      {inspecting && <div className="status info">Reading files…</div>}

      {cl && tp && !inspecting && (
        <>
          <table className="mapping">
            <thead>
              <tr>
                <th>Class list column</th>
                <th>Put it in the report as</th>
              </tr>
            </thead>
            <tbody>
              {sources.map((s) => {
                const m = mappings[s] ?? { checked: false, target: "" };
                return (
                  <tr key={s} className={m.checked ? "" : "off"}>
                    <td>
                      <label className="check">
                        <input type="checkbox" checked={m.checked}
                               onChange={(e) => setMapping(s, { checked: e.target.checked })} />
                        {s}
                      </label>
                    </td>
                    <td>
                      <select value={m.target} disabled={!m.checked} aria-label={`Report column for ${s}`}
                              onChange={(e) => setMapping(s, { target: e.target.value })}>
                        <option value="">➕ New column “{s}”</option>
                        <optgroup label="Override existing column">
                          {targets.map((t) => (
                            <option key={t} value={t}>{t}</option>
                          ))}
                        </optgroup>
                      </select>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>

          <div className="form">
            <div className="form-row">
              <label className="check">
                <input
                  type="checkbox"
                  checked={totalOn}
                  onChange={(e) => {
                    setTotalOn(e.target.checked);
                    if (e.target.checked) openTotalDialog();
                  }}
                />
                Add total column
              </label>
              {totalOn && (
                <div className="inline">
                  <span className="muted">
                    {effectiveSel.length
                      ? `${totalName || DEFAULT_TOTAL_NAME} = ` +
                        effectiveSel.map((id) => id.slice(2)).join(" + ")
                      : "No columns chosen"}
                  </span>
                  <button type="button" className="link" onClick={openTotalDialog}>Edit</button>
                </div>
              )}
            </div>
          </div>
        </>
      )}

      {problems.length > 0 && classlist && template && !inspecting && (
        <ul className="problems">
          {problems.map((p) => <li key={p}>{p}</li>)}
        </ul>
      )}

      <div className="row">
        <button className="primary" disabled={busy || inspecting || problems.length > 0} onClick={run}>
          {busy ? "Working…" : "Generate grade report"}
        </button>
      </div>

      {error && <div className="status error">{error}</div>}
      {result && <TaskResultView result={result} />}

      <dialog ref={dialogRef} className="dialog" onCancel={(e) => { e.preventDefault(); closeDialog(false); }}>
        <h2>Total column</h2>
        <div className="form-row">
          <label htmlFor="an-total-name">Column name</label>
          <input id="an-total-name" type="text" value={totalName} onChange={(e) => setTotalName(e.target.value)} />
        </div>
        <p className="muted">Tick the columns to add up for each student:</p>
        {candidates.length === 0 && <p className="muted">No numeric columns available — tick a class list column first.</p>}
        <ul className="choices">
          {candidates.map((c) => (
            <li key={c.id}>
              <label className="check">
                <input
                  type="checkbox"
                  checked={draftSel.includes(c.id)}
                  onChange={(e) =>
                    setDraftSel((sel) => (e.target.checked ? [...sel, c.id] : sel.filter((x) => x !== c.id)))
                  }
                />
                <span>
                  {c.label}
                  {c.hint && <span className="muted"> ({c.hint})</span>}
                </span>
              </label>
            </li>
          ))}
        </ul>
        <div className="row">
          <button className="primary" disabled={!draftSel.length} onClick={() => closeDialog(true)}>Save</button>
          <button className="secondary" onClick={() => closeDialog(false)}>Cancel</button>
        </div>
      </dialog>
    </>
  );
}
