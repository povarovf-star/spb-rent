// Прокси к модельному API: страница на https не может ходить на http-VPS
// напрямую (mixed content), поэтому запрос идёт через serverless-функцию.
// Адрес сервера задаётся переменной окружения MODEL_API_URL в настройках Vercel.
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
