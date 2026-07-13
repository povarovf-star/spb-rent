// Proxy to the model API: an https page cannot call an http VPS directly
// (mixed content), so the request goes through a serverless function.
// The server address is set by the MODEL_API_URL env var in the Vercel settings.
const API_URL = process.env.MODEL_API_URL;

function normalizeInterval(data) {
  const low = Number(data.price_low);
  const high = Number(data.price_high);
  const fair = Number(data.fair_price);
  if (!Number.isFinite(low) || !Number.isFinite(high)) return data;
  const values = [low, high];
  if (Number.isFinite(fair)) values.push(fair);
  data.price_low = Math.min(...values);
  data.price_high = Math.max(...values);
  return data;
}

export default async function handler(req, res) {
  if (req.method !== "POST") {
    res.status(405).json({ detail: "Используйте POST" });
    return;
  }
  if (!API_URL) {
    res.status(503).json({ detail: "MODEL_API_URL не настроен" });
    return;
  }
  try {
    const upstream = await fetch(`${API_URL}/predict`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req.body),
      signal: AbortSignal.timeout(25000),
    });
    const data = await upstream.json();
    res.status(upstream.status).json(
      upstream.ok && data && typeof data === "object" ? normalizeInterval(data) : data,
    );
  } catch (err) {
    res.status(503).json({ detail: `Модель временно недоступна: ${err.message}` });
  }
}
