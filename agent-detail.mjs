import { loadEnv, getJson } from "./env.mjs";

loadEnv();

async function main() {
  const tokenId = process.argv[2];
  if (!tokenId) {
    console.error("Usage: node agent-detail.mjs <tokenId>");
    process.exit(1);
  }
  const chainId = process.argv[3] || "56";
  const data = await getJson(`/agents/${chainId}/${tokenId}`);
  console.log(JSON.stringify(data, null, 2));
}

main().catch((e) => {
  console.error("ERROR:", e.message);
  process.exit(1);
});