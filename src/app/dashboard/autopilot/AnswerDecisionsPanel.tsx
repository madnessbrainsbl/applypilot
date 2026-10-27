import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Button } from "@/components/ui/button";

export type AnswerDecision = {
  question_key: string; job_url: string; question: string; company: string;
  persona: string; source: string; method: string; status: string;
  draft: string; answer: string; reason: string; evidence: string[]; cv: string;
};

const SOURCES: Record<string, string> = { cache: "Saved answer", llm: "AI", rules: "Rules", none: "No answer" };

export default function AnswerDecisionsPanel({ decisions }: { decisions: AnswerDecision[] }) {
  return (
    <Card lang="en">
      <CardHeader>
        <CardTitle>Answer review</CardTitle>
        <CardDescription>Decision reasons and evidence from the selected persona CV. Latest 30 checks.</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {decisions.length === 0 ? <p className="text-sm text-muted-foreground">Checks appear after application questions are filled.</p> : null}
        {decisions.map((d) => (
          <Collapsible key={`${d.question_key}:${d.job_url}`} className="flex flex-col gap-2 rounded-md border p-3">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium break-words">{d.question}</span>
              <Badge variant={d.status === "manual" ? "destructive" : "secondary"}>
                {d.status === "manual" ? "Needs review" : d.status === "revised" ? "Revised" : d.method === "cv_review" ? "CV verified" : "Rules checked"}
              </Badge>
            </div>
            <p className="text-xs text-muted-foreground">{d.company || "Any company"} · {d.persona} · {SOURCES[d.source] || d.source}{d.cv ? ` · ${d.cv}` : ""}</p>
            <p className="break-words text-sm">{d.reason}</p>
            <CollapsibleTrigger asChild>
              <Button variant="ghost" size="sm" className="self-start" aria-label={`Evidence: ${d.question}`}>Show evidence</Button>
            </CollapsibleTrigger>
            <CollapsibleContent className="flex flex-col gap-3 text-sm">
              {d.answer ? <div><p className="font-medium">Verified answer</p><p className="whitespace-pre-wrap break-words">{d.answer}</p></div> : null}
              {d.draft && d.draft !== d.answer ? <div><p className="font-medium">Original draft</p><p className="whitespace-pre-wrap break-words text-muted-foreground">{d.draft}</p></div> : null}
              {d.evidence?.length ? <div><p className="font-medium">CV evidence</p>
                {d.evidence.map((quote, i) => <blockquote key={i} className="my-2 border-l-2 pl-3 whitespace-pre-wrap break-words">{quote}</blockquote>)}
              </div> : null}
            </CollapsibleContent>
          </Collapsible>
        ))}
      </CardContent>
    </Card>
  );
}
