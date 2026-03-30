/**
 * Edge Function: intellicore-monitor
 * Chamada pelo pg_cron diariamente às 10h UTC (07h BRT).
 * Dispara o backend Render que executa o monitor de menções Python.
 */

const RENDER_URL = "https://monitor-de-mencoes.onrender.com";
const INTERNAL_API_KEY = Deno.env.get("INTERNAL_API_KEY") ?? "";

Deno.serve(async (req: Request) => {
  // Aceita apenas POST (chamado pelo pg_cron via net.http_post)
  if (req.method !== "POST") {
    return new Response(JSON.stringify({ error: "Method not allowed" }), {
      status: 405,
      headers: { "Content-Type": "application/json" },
    });
  }

  const startTime = Date.now();
  console.log(`[intellicore-monitor] Iniciando às ${new Date().toISOString()}`);

  try {
    const response = await fetch(`${RENDER_URL}/api/internal/run-monitor`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Internal-Key": INTERNAL_API_KEY,
      },
      body: JSON.stringify({ source: "pg_cron", timestamp: new Date().toISOString() }),
      // Timeout de 300s (5 min) — o monitor pode demorar
      signal: AbortSignal.timeout(300_000),
    });

    const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
    const body = await response.text();

    console.log(`[intellicore-monitor] Resposta ${response.status} em ${elapsed}s: ${body.slice(0, 200)}`);

    return new Response(
      JSON.stringify({
        status: response.status,
        elapsed_s: parseFloat(elapsed),
        render_response: body.slice(0, 500),
      }),
      {
        status: response.ok ? 200 : 502,
        headers: { "Content-Type": "application/json" },
      }
    );
  } catch (err) {
    const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
    const msg = err instanceof Error ? err.message : String(err);
    console.error(`[intellicore-monitor] Erro após ${elapsed}s: ${msg}`);

    return new Response(
      JSON.stringify({ error: msg, elapsed_s: parseFloat(elapsed) }),
      {
        status: 500,
        headers: { "Content-Type": "application/json" },
      }
    );
  }
});
