import { FlaskConical } from "lucide-react";
import { EmptyState, PageHeader, Panel } from "@/components/ui";
export default function ResearchLabPage(){return <><PageHeader eyebrow="ISOLATED EXPERIMENTS" title="Research Lab" detail="Experimental hypotheses stay separate from the production playbook until robustly tested."/><Panel><div className="mb-5 flex justify-center text-slate-700"><FlaskConical size={36}/></div><EmptyState title="Research Lab is reserved" detail="No experimental signal is connected to live coaching or execution policy."/></Panel></>}
