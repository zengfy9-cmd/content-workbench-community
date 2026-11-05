export type BenchmarkPassage = { key: string; index: number; markdown: string; html: string; text: string }
export type BenchmarkCard = { key: string; role: string; purpose: string; technique: string; evidence: string; adaptation: string; needs: string[] }
export type BenchmarkReview = { analysis_id: number; paragraph_key: string; note: string; useful: number; reviewed: number; decision?:string;judgment?:string|{agree?:string;correction?:string;verify?:string} }
export type ResearchMetadata={depth:string;model:string;backend?:string;origin?:string;rule_version:string;rule_hash?:string;rules?:string}
export type BasicResearch={thesis:string;audience:string;hook:string;structure:string;techniques:string;evidence:string;further_research:string;basis:{field:string;paragraph_key:string;quote:string}[]}
export type BenchmarkSummary = {
  extraction_only?:number; media_id?: number | null; source_platform?:string;source_kind?:string;target_platforms?:string[]
  read_count?: number | null; read_count_at?: number | null; original_html?: string
  id: number; title: string; url: string; author: string; kind: string; tags: string[]; archived: number
  analysis_state: 'idle' | 'queued' | 'running' | 'done' | 'failed' | 'paused'; analysis_error: string | null
  current_analysis_id: number | null; created_at: number; updated_at: number; preview: string
}
export type Benchmark = BenchmarkSummary & {
  full_report?: Benchmark | null
  body: string; body_hash: string; source: string; stale: boolean; analysis_progress: string; passages: BenchmarkPassage[]
  analysis: { id: number; created_at: number; body_hash: string; scope: 'full' | 'selection'; research?: ResearchReport; basic?:BasicResearch;metadata?:ResearchMetadata; overview: Record<string, string>; paragraphs: BenchmarkCard[] } | null
  reviews: BenchmarkReview[]; versions: { id: number; created_at: number; body_hash: string }[]
}
export type BenchmarkLink = { draft_id: number; benchmark_id: number; analysis_id: number | null; title: string; author: string; kind: string; url: string }

export type ResearchReport = { audience:string; thesis:string; purpose:string; emotion:string; evidence:string; golden_quotes:string[]; reference_value:string; writing_prep:string; sections:{location:string;summary:string;role:string;technique:string;paragraph_key:string}[] }
