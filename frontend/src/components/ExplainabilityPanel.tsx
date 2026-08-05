import { ShieldCheckIcon, SparkleIcon } from '@phosphor-icons/react'

const EXAMPLE_PROMPTS = [
  'Summarize the fault evidence',
  'Explain the evidence score',
  'What should the crew verify?',
]

export function ExplainabilityPanel() {
  return (
    <section
      className="panel explainability-panel"
      aria-labelledby="explainability-title"
      data-static-preview="true"
    >
      <div className="explainability-heading">
        <span className="explainability-icon" aria-hidden="true">
          <SparkleIcon size={19} weight="duotone" />
        </span>
        <div>
          <p className="section-label">AI explainability</p>
          <h2 id="explainability-title">Incident explanation assistant</h2>
        </div>
        <span className="preview-badge">Frontend preview</span>
      </div>

      <div className="explainability-content">
        <div className="example-explanation">
          <span>Example response</span>
          <strong>Why is this the probable root fault?</strong>
          <p>
            A concise operator explanation will appear here after the AI integration is connected.
            It will translate the structured incident evidence without changing the selected fault,
            score, or ticket state.
          </p>
        </div>

        <div className="explainability-prompts" aria-label="Planned explanation prompts">
          {EXAMPLE_PROMPTS.map((prompt) => (
            <span key={prompt}>{prompt}</span>
          ))}
        </div>

        <div className="explainability-boundary">
          <ShieldCheckIcon size={18} weight="duotone" aria-hidden="true" />
          <p>
            <strong>System evidence remains authoritative.</strong>
            <span>This preview is static and makes no operational decisions.</span>
          </p>
        </div>
      </div>
    </section>
  )
}
