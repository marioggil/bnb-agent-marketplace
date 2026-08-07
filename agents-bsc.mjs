import { loadEnv, getJson } from "./env.mjs";

loadEnv();

const abbr = (addr) => (addr ? `${addr.slice(0, 6)}...${addr.slice(-4)}` : "n/a");
const fmt = (s) => (s ? s.slice(0, 10) : "n/a");

// Fields observed for a marketplace; printed as a compact table plus the full field list.
const WANTED = [
  "id", "agent_id", "token_id", "chain_id", "contract_address", "owner_address",
  "owner_ens", "name", "description", "image_url", "is_verified", "star_count",
  "supported_protocols", "x402_supported", "total_score", "rank", "health_score",
  "total_feedbacks", "average_score", "cross_chain_versions", "created_at", "updated_at",
];

async function main() {
  const limit = Number(process.argv[2] || 30);
  const agents = await getJson(`/agents?limit=${Math.max(limit, 1)}&chain_id=56`);
  const bsc = agents.filter((a) => a.chain_id === 56);

  if (bsc.length === 0) {
    console.log(`No BSC (chain_id=56) agents among the ${agents.length} returned.`);
    return;
  }

  console.log(`BSC agents found: ${bsc.length} (of ${agents.length} items fetched)\n`);
  console.log("Table: name | token_id | x402 | protocols | avg_score | feedbacks | owner | created");
  console.log("-".repeat(120));
  for (const a of bsc) {
    const prots = (a.supported_protocols || []).join(",") || "-";
    console.log(
      [
        (a.name || "(unnamed)").slice(0, 34).padEnd(34),
        String(a.token_id).padStart(8),
        (a.x402_supported ? "yes" : "no").padStart(3),
        prots.padEnd(12),
        String(a.average_score ?? "n/a").padStart(5),
        String(a.total_feedbacks ?? 0).padStart(6),
        abbr(a.owner_address).padEnd(13),
        fmt(a.created_at),
      ].join(" | ")
    );
  }

  console.log(`\n=== Field map (${WANTED.length} fields) ${"agent_id"} = agent_id example ===`);
  console.log("agent_id example:", bsc[0]?.agent_id);
}

main().catch((e) => {
  console.error("ERROR:", e.message);
  process.exit(1);
});