"use client";

import { useState, useTransition } from "react";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { runBrandQueryAction } from "@/lib/actions/brand";
import type { BrandQueryResponse, RetrievedChunkInfo } from "@/lib/types";

interface GenerateTabProps {
  clientId: string;
}

const MAX_PROMPT_LEN = 10_000;

/**
 * Tab "Genera" del Brand Brain (S7 step 7b).
 *
 * Flow: utente scrive prompt → click "Genera" → Server Action `runBrandQueryAction`
 * → backend RAG (embed → retrieval → Claude) → render output + metadata.
 *
 * Stato lifecycle: tre stati mutuamente esclusivi (idle / pending / result|error).
 * Niente streaming in S7 (la response è bloccante, latency tipica 2-8s su Sonnet
 * con max_tokens default).
 */
export function GenerateTab({ clientId }: GenerateTabProps) {
  const [prompt, setPrompt] = useState("");
  const [pending, startTransition] = useTransition();
  const [result, setResult] = useState<BrandQueryResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setResult(null);
    startTransition(async () => {
      const state = await runBrandQueryAction(clientId, prompt);
      if (state.error) {
        setError(state.error);
      } else if (state.success) {
        setResult(state.success);
      }
    });
  }

  const isEmpty = prompt.trim().length === 0;
  const isOverLimit = prompt.length > MAX_PROMPT_LEN;

  return (
    <div className="mt-6 space-y-6">
      <form onSubmit={handleSubmit} className="space-y-3">
        <Label htmlFor="prompt" className="text-sm font-medium">
          Cosa vuoi generare?
        </Label>
        <Textarea
          id="prompt"
          name="prompt"
          placeholder="Es. Genera 3 caption Instagram per il summer drop, max 80 caratteri ciascuna."
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          className="min-h-[120px] font-mono text-sm"
          disabled={pending}
          aria-invalid={isOverLimit ? true : undefined}
          aria-describedby="prompt-counter"
        />
        <div className="flex items-center justify-between">
          <p
            id="prompt-counter"
            className={
              isOverLimit
                ? "text-destructive text-xs"
                : "text-muted-foreground text-xs"
            }
          >
            {prompt.length.toLocaleString("it-IT")} / {MAX_PROMPT_LEN.toLocaleString("it-IT")}{" "}
            caratteri
          </p>
          <Button type="submit" disabled={isEmpty || isOverLimit || pending}>
            {pending ? "Generazione…" : "Genera"}
          </Button>
        </div>
      </form>

      {error && (
        <div
          role="alert"
          className="border-destructive/50 bg-destructive/10 rounded-lg border p-4"
        >
          <p className="text-destructive text-sm font-medium">Errore generazione</p>
          <p className="text-destructive/80 mt-1 text-xs">{error}</p>
        </div>
      )}

      {result && <ResultPanel result={result} />}
    </div>
  );
}

function ResultPanel({ result }: { result: BrandQueryResponse }) {
  return (
    <div className="space-y-4">
      <article className="bg-card rounded-lg border p-6">
        <h3 className="text-muted-foreground mb-3 text-xs font-medium tracking-wide uppercase">
          Output Brand Brain
        </h3>
        <div className="text-foreground text-sm leading-relaxed whitespace-pre-wrap">
          {result.output_text}
        </div>
      </article>

      <div className="grid grid-cols-2 gap-3 text-xs md:grid-cols-4">
        <MetaCard label="Modello" value={result.model_used} />
        <MetaCard label="Latency" value={`${(result.latency_ms / 1000).toFixed(2)}s`} />
        <MetaCard
          label="Tokens in/out"
          value={`${result.tokens_input.toLocaleString("it-IT")} / ${result.tokens_output.toLocaleString("it-IT")}`}
        />
        <MetaCard
          label="Chunks usati"
          value={`${result.retrieved_chunks.length}${result.form_data_used ? " + brand form" : ""}`}
        />
      </div>

      {result.retrieved_chunks.length > 0 && (
        <details className="bg-muted/30 rounded-lg border p-4">
          <summary className="text-muted-foreground cursor-pointer text-xs font-medium">
            Reference chunks usati ({result.retrieved_chunks.length})
          </summary>
          <ul className="mt-3 space-y-2">
            {result.retrieved_chunks.map((c, i) => (
              <li key={`${c.asset_id}:${c.chunk_index}`}>
                <ChunkRow index={i + 1} chunk={c} />
              </li>
            ))}
          </ul>
        </details>
      )}

      <p className="text-muted-foreground/80 font-mono text-[10px]">
        generation_id: {result.generation_id}
      </p>
    </div>
  );
}

function MetaCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-muted/30 rounded border p-2">
      <p className="text-muted-foreground text-[10px] tracking-wide uppercase">{label}</p>
      <p className="mt-1 font-mono text-sm break-words">{value}</p>
    </div>
  );
}

function ChunkRow({ index, chunk }: { index: number; chunk: RetrievedChunkInfo }) {
  return (
    <div className="bg-background rounded border p-2 text-xs">
      <div className="text-muted-foreground flex items-baseline justify-between gap-3">
        <span className="font-mono">#{index}</span>
        <span className="text-foreground/80 truncate font-medium" title={chunk.asset_filename}>
          {chunk.asset_filename}
        </span>
        <span className="font-mono whitespace-nowrap">
          chunk {chunk.chunk_index} · sim {chunk.similarity.toFixed(3)}
        </span>
      </div>
    </div>
  );
}
