const architecture = ['FastAPI', 'Redis Streams', 'PostgreSQL', 'React']

export function App() {
  return (
    <main className="app-shell">
      <section className="hero" aria-labelledby="page-title">
        <p className="eyebrow">Outage localization system</p>
        <h1 id="page-title">Propel operator console</h1>
        <p className="summary">
          The foundation is online. The first vertical slice will turn surveyed-network
          telemetry into one localized incident and a telemetry-verified ticket.
        </p>

        <dl className="status-grid" aria-label="Foundation status">
          <div>
            <dt>Current milestone</dt>
            <dd>VS-01 · Foundation</dd>
          </div>
          <div>
            <dt>System state</dt>
            <dd className="healthy"><span aria-hidden="true" />Ready to build</dd>
          </div>
        </dl>

        <ul className="stack" aria-label="Application stack">
          {architecture.map((technology) => (
            <li key={technology}>{technology}</li>
          ))}
        </ul>
      </section>
    </main>
  )
}

