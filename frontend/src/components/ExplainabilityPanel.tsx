import { ShieldCheckIcon, SparkleIcon } from '@phosphor-icons/react'

import type { Incident, IncidentExplanation } from '../api/types'

interface ExplainabilityPanelProps {
  incident: Incident | null
  explanation: IncidentExplanation | null
  loading: boolean
  error: string | null
}

export function ExplainabilityPanel({
  incident,
  explanation,
  loading,
  error,
}: ExplainabilityPanelProps) {
  return (
    <section
      className="panel explainability-panel"
      aria-labelledby="explainability-title"
    >
      <div className="explainability-heading">
        <span className="explainability-icon" aria-hidden="true">
          <SparkleIcon size={19} weight="duotone" />
        </span>
        <div>
          <p className="section-label">AI explainability</p>
          <h2 id="explainability-title">Incident explanation assistant</h2>
        </div>
        {explanation && (
          <span className={`preview-badge${explanation.source === 'AI_GENERATED' ? ' ai' : ''}`}>
            {explanation.source === 'AI_GENERATED' ? 'AI generated' : 'Deterministic fallback'}
          </span>
        )}
      </div>

      <div className="explainability-content">
        {!incident && (
          <div className="example-explanation explainability-empty">
            <strong>Select a current finding</strong>
            <p>Its evidence and ticket workflow will be explained here in plain language.</p>
          </div>
        )}
        {incident && loading && (
          <div className="loading-block" role="status">
            <span className="spinner" aria-hidden="true" />
            Explaining this finding…
          </div>
        )}
        {incident && error && !loading && (
          <div className="example-explanation explainability-error" role="alert">
            <strong>Explanation unavailable</strong>
            <p>{error}</p>
          </div>
        )}
        {incident && explanation && !loading && (
          <div className="explanation-sections">
            <article>
              <span>What happened</span>
              <p>{explanation.what_happened}</p>
            </article>
            <article>
              <span>Why Propel chose this probable cause</span>
              <p>{explanation.why_this_cause}</p>
            </article>
            <article>
              <span>What happens next</span>
              <p>{explanation.what_happens_next}</p>
            </article>
          </div>
        )}

        <div className="explainability-boundary">
          <ShieldCheckIcon size={18} weight="duotone" aria-hidden="true" />
          <p>
            <strong>System evidence remains authoritative.</strong>
            <span>Generated text makes no localization, score, or ticket decisions.</span>
          </p>
        </div>
      </div>
    </section>
  )
}
