import { loadEnv, getJson } from "./env.mjs";

loadEnv();

async function main() {
  const stats = await getJson("/stats");
  if (!stats) throw new Error("No stats returned");

  const num = (v) => (v == null ? "n/a" : Number(v).toLocaleString("en-US"));

  console.log("=== ERC-8004 Protocol Stats (8004scan) ===");
  console.log(`Total agents        : ${num(stats.total_agents)}`);
  console.log(`Total users         : ${num(stats.total_users)}`);
  console.log(`Total feedbacks     : ${num(stats.total_feedbacks)}`);
  console.log(`Total validations   : ${num(stats.total_validations)}`);
  console.log(`Average feedback score: ${stats.average_feedback_score != null ? Number(stats.average_feedback_score).toFixed(2) : "n/a"}`);
  console.log(`Daily new agents    : ${num(stats.daily_new_agents)}`);
  console.log(`Daily new users     : ${num(stats.daily_new_users)}`);
  console.log(`Daily feedbacks     : ${num(stats.daily_feedbacks)}`);

  const chains = stats.supported_chains || [];
  const withRegistry = chains.filter((c) => c.has_registry === true);
  console.log(`\nChains total        : ${chains.length}`);
  console.log(`Chains has_registry : ${withRegistry.length}`);
  console.log("\nSupported chains (has_registry=true):");
  for (const c of withRegistry) {
    console.log(`  ${String(c.chain_id).padEnd(8)} ${c.name.padEnd(22)} ${c.key}`);
  }
}

main().catch((e) => {
  console.error("ERROR:", e.message);
  process.exit(1);
});