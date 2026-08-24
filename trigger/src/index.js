// Cron trigger for the job radar.
//
// GitHub throttles scheduled workflows hard on public repositories: a
// "*/15" schedule was measured firing every 50 minutes on average, with gaps
// up to 73. A workflow_dispatch is not throttled that way, so this worker
// asks for a run on a schedule Cloudflare actually keeps.
//
// The worker does no scanning itself. It sends one API call; the scan stays in
// Actions where the code, the secrets and the ledger already live.

const API = "https://api.github.com/repos/{repo}/actions/workflows/{workflow}/dispatches";

async function dispatch(env) {
  const url = API.replace("{repo}", env.REPO).replace("{workflow}", env.WORKFLOW);
  const response = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      // GitHub rejects API requests without one.
      "User-Agent": "job-radar-trigger",
    },
    body: JSON.stringify({ ref: env.BRANCH ?? "main" }),
  });

  // 204 is success and carries no body.
  if (response.status !== 204) {
    const detail = await response.text();
    throw new Error(`dispatch failed: HTTP ${response.status} ${detail.slice(0, 200)}`);
  }
}

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(dispatch(env));
  },

  // Manual check: visiting the worker's URL triggers a run and reports back,
  // so the setup can be verified without waiting for the next tick.
  async fetch(request, env) {
    try {
      await dispatch(env);
      return new Response("dispatched\n", { status: 200 });
    } catch (error) {
      return new Response(`${error.message}\n`, { status: 502 });
    }
  },
};
