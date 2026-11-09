import { useEffect, useState } from "react";
import "./App.css";
import AddGradesTab from "./tabs/AddGradesTab";
import AnnounceTab from "./tabs/AnnounceTab";
import ConvertTab from "./tabs/ConvertTab";

const TABS = [
  { id: "convert", title: "DOCX → Moodle XML", short: "DOCX → XML", Component: ConvertTab },
  { id: "add-grades", title: "Add grade to class list", short: "Add grades", Component: AddGradesTab },
  { id: "announce", title: "Announce grades", short: "Announce", Component: AnnounceTab },
] as const;

type TabId = (typeof TABS)[number]["id"];

function tabFromHash(): TabId {
  const h = window.location.hash.replace("#", "");
  return (TABS.find((t) => t.id === h)?.id ?? "convert") as TabId;
}

export default function App() {
  const [tab, setTab] = useState<TabId>(tabFromHash);

  useEffect(() => {
    const onHash = () => setTab(tabFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  return (
    <>
      <header className="topbar">
        <div className="topbar-inner">
          <div className="brand">
            <span className="brand-mark" aria-hidden="true">M</span>
            MIU Exam Tools
          </div>
          <nav className="tabs" role="tablist">
            {TABS.map((t) => (
              <a
                key={t.id}
                href={`#${t.id}`}
                role="tab"
                aria-selected={tab === t.id}
                className={"tab" + (tab === t.id ? " active" : "")}
              >
                <span className="long">{t.title}</span>
                <span className="short">{t.short}</span>
              </a>
            ))}
          </nav>
        </div>
      </header>
      <main className="app">
        <div className="card">
          {/* Keep every tab mounted so switching tabs doesn't lose chosen files. */}
          {TABS.map(({ id, title, Component }) => (
            <section key={id} hidden={tab !== id} role="tabpanel">
              <h1>{title}</h1>
              <Component />
            </section>
          ))}
        </div>
      </main>
    </>
  );
}
