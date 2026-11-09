import { useRef, useState } from "react";

interface Props {
  label?: string;
  file: File | null;
  accept: string[]; // extensions, e.g. [".xlsx"]
  onFile: (f: File | null) => void;
  onError?: (msg: string) => void;
  info?: React.ReactNode;
  compact?: boolean;
}

export default function FileDrop({ label, file, accept, onFile, onError, info, compact }: Props) {
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const pick = (f: File | undefined | null) => {
    if (!f) return;
    if (!accept.some((ext) => f.name.toLowerCase().endsWith(ext))) {
      onError?.(`“${f.name}” is not a ${accept.join(" / ")} file.`);
      return;
    }
    onFile(f);
  };

  return (
    <div className="field">
      {label && <div className="field-label">{label}</div>}
      <div
        className={"dropzone" + (dragging ? " dragging" : "") + (compact ? " compact" : "")}
        role="button"
        tabIndex={0}
        onClick={() => inputRef.current?.click()}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            inputRef.current?.click();
          }
        }}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          pick(e.dataTransfer.files?.[0]);
        }}
      >
        <input
          ref={inputRef}
          type="file"
          accept={accept.join(",")}
          style={{ display: "none" }}
          onChange={(e) => {
            pick(e.target.files?.[0]);
            e.target.value = "";
          }}
        />
        {file ? (
          <>
            <div className="picked">{file.name}</div>
            <div className="hint">
              {(file.size / 1024).toFixed(1)} KB — click or drop to replace
            </div>
          </>
        ) : (
          <>
            <div className="picked">Click to choose a file or drop it here</div>
            <div className="hint">{accept.join(" / ")}</div>
          </>
        )}
      </div>
      {info && <div className="field-info">{info}</div>}
    </div>
  );
}
