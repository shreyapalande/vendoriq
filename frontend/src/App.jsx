import { useCallback, useEffect, useState } from 'react'

// Ask the backend (through the Vite proxy) whether it and the database are up.
// Returns one of:
//   { kind: 'ok' | 'degraded', body }        200 or 503 from /api/v1/health
//   { kind: 'error', message, requestId }    anything else, incl. API unreachable
async function fetchHealth() {
  let response
  try {
    response = await fetch('/api/v1/health')
  } catch {
    return { kind: 'error', message: 'Could not reach the server.' }
  }

  const body = await response.json().catch(() => null)
  if (response.status === 200) return { kind: 'ok', body }
  if (response.status === 503 && body?.status) return { kind: 'degraded', body }
  if (body?.error) {
    // The backend's shared error shape: {"error": {"code", "message", "request_id"}}
    return { kind: 'error', message: body.error.message, requestId: body.error.request_id }
  }
  // No JSON: usually the Vite proxy answering because FastAPI isn't running.
  return { kind: 'error', message: `API not reachable (HTTP ${response.status}). Is the backend running?` }
}

function StatusRow({ label, ok, text }) {
  return (
    <div className="flex items-center justify-between py-2">
      <span className="text-slate-600">{label}</span>
      <span className={`flex items-center gap-2 font-medium ${ok ? 'text-emerald-700' : 'text-red-700'}`}>
        <span className={`h-2.5 w-2.5 rounded-full ${ok ? 'bg-emerald-500' : 'bg-red-500'}`} />
        {text}
      </span>
    </div>
  )
}

export default function App() {
  const [health, setHealth] = useState(null) // null while loading

  const check = useCallback(async () => {
    setHealth(null)
    setHealth(await fetchHealth())
  }, [])

  useEffect(() => {
    // Run the check once on page load. fetchHealth() is async, so state is only
    // set after the request finishes, not synchronously inside the effect.
    let cancelled = false
    fetchHealth().then((result) => {
      if (!cancelled) setHealth(result)
    })
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <main className="min-h-screen bg-slate-50 px-4 py-16">
      <div className="mx-auto max-w-md">
        <h1 className="text-2xl font-semibold text-slate-900">VendorIQ</h1>
        <p className="mt-1 text-sm text-slate-500">Vendor security review</p>

        <section className="mt-8 rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
          <div className="flex items-center justify-between">
            <h2 className="font-medium text-slate-900">System status</h2>
            <button
              type="button"
              onClick={check}
              disabled={health === null}
              className="rounded-md border border-slate-300 px-3 py-1 text-sm text-slate-700 hover:bg-slate-100 disabled:opacity-50"
            >
              Check again
            </button>
          </div>

          <div className="mt-3 divide-y divide-slate-100 text-sm">
            {health === null && <p className="py-2 text-slate-500">Checking…</p>}

            {(health?.kind === 'ok' || health?.kind === 'degraded') && (
              <>
                <StatusRow label="API" ok text="reachable" />
                <StatusRow label="Database" ok={health.body.database === 'ok'} text={health.body.database} />
              </>
            )}

            {health?.kind === 'error' && (
              <div className="py-2">
                <StatusRow label="API" ok={false} text="error" />
                <p className="text-red-700">{health.message}</p>
                {health.requestId && (
                  <p className="mt-1 text-xs text-slate-500">Request ID: {health.requestId}</p>
                )}
              </div>
            )}
          </div>
        </section>
      </div>
    </main>
  )
}
