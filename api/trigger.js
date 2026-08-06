/**
 * Vercel Cron 触发器 — 每日 09:20 CST 精准触发 GitHub Actions
 *
 * 背景：GitHub Actions schedule 事件在公开仓库上延迟 9-12 小时，
 * 且因 concurrency: cancel-in-progress 被自链取消，导致 cron 形同虚设。
 * Vercel Cron 不受此影响，可精准按时触发。
 */

const GITHUB_TOKEN = process.env.GH_PAT;
const REPO = "crackpot-bot/exchange-rate";
const WORKFLOW_ID = "collect.yml";
const REF = "main";

// 检查数据是否较新（30分钟内），避免重复触发
async function isRecentData() {
  try {
    const res = await fetch(
      `https://crackpot-bot.github.io/exchange-rate/data/latest.json?_=${Date.now()}`
    );
    if (!res.ok) return false;

    const data = await res.json();
    if (!Array.isArray(data) || data.length === 0) return false;

    const fetchedAt = new Date(data[0].fetched_at + "+08:00");
    const now = new Date();
    const diffMs = now.getTime() - fetchedAt.getTime();
    const diffMin = diffMs / 1000 / 60;

    console.log(`最新数据: ${data[0].fetched_at}, 距今 ${diffMin.toFixed(1)} 分钟`);
    return diffMin < 30;
  } catch (e) {
    console.error("检查数据失败:", e.message);
    return false;
  }
}

export default async function handler(req, res) {
  // Vercel Cron 每次请求带 Authorization header (Bearer token)
  // 验证是否为 Vercel Cron 调用
  const authHeader = req.headers.authorization || "";
  if (authHeader !== `Bearer ${process.env.CRON_SECRET}`) {
    // 也允许手动浏览器访问（方便调试）
    // return res.status(401).json({ error: "Unauthorized" });
  }

  console.log("⏰ 触发时间:", new Date().toISOString());

  // 1. 先检查是否已经有新数据
  const recent = await isRecentData();
  if (recent) {
    console.log("✅ 数据已是最新（30分钟内），跳过触发");
    return res.status(200).json({
      ok: true,
      triggered: false,
      reason: "数据已是最新",
      time: new Date().toISOString(),
    });
  }

  // 2. 触发 GitHub Actions
  if (!GITHUB_TOKEN) {
    console.error("❌ GH_PAT 环境变量未设置");
    return res.status(500).json({
      ok: false,
      error: "GH_PAT not configured",
      time: new Date().toISOString(),
    });
  }

  try {
    const dispatchRes = await fetch(
      `https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW_ID}/dispatches`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${GITHUB_TOKEN}`,
          Accept: "application/vnd.github+json",
          "X-GitHub-Api-Version": "2022-11-28",
          "User-Agent": "vercel-cron-trigger/1.0",
        },
        body: JSON.stringify({ ref: REF }),
      }
    );

    if (dispatchRes.ok || dispatchRes.status === 204) {
      console.log("✅ 已触发 workflow_dispatch");
      return res.status(200).json({
        ok: true,
        triggered: true,
        status: dispatchRes.status,
        time: new Date().toISOString(),
      });
    } else {
      const body = await dispatchRes.text();
      console.error(`❌ GitHub API 返回 ${dispatchRes.status}:`, body);
      return res.status(502).json({
        ok: false,
        triggered: false,
        error: `GitHub API: ${dispatchRes.status}`,
        time: new Date().toISOString(),
      });
    }
  } catch (e) {
    console.error("❌ 触发失败:", e.message);
    return res.status(500).json({
      ok: false,
      triggered: false,
      error: e.message,
      time: new Date().toISOString(),
    });
  }
}
