// Lista agentes ERC-8004 en BNB Chain (56) desde 8004scan
// Filtra los que tienen reputación útil: con feedbacks, score, o wallet activo
//
// Uso: node scripts/list_bnb_agents.mjs [limit] [chainId]
//   limit   = cuántos agentes traer (default 100, max 100 por request)
//   chainId = chain a consultar (default 56 = BNB mainnet, 97 = testnet)

const LIMIT = Math.min(parseInt(process.argv[2] ?? "100", 10), 100);
const CHAIN_ID = parseInt(process.argv[3] ?? "56", 10);

const url = `https://api.8004scan.io/api/v1/agents?chain_id=${CHAIN_ID}&limit=${LIMIT}`;

console.log(`\n🔍 Buscando hasta ${LIMIT} agentes en chain ${CHAIN_ID} (8004scan.io)...\n`);

try {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const data = await res.json();
  const items = data.items ?? [];
  const total = data.total ?? "?";

  console.log(`📊 Total en chain: ${total} | Devueltos: ${items.length}\n`);

  // Filtrar agentes con datos útiles (descartar los recién registrados sin nada)
  const ranked = items
    .filter(a => a.total_score > 0 || a.total_feedbacks > 0 || a.star_count > 0)
    .sort((a, b) => (b.total_score ?? 0) - (a.total_score ?? 0));

  const fresh = items.filter(a => a.total_score === 0 && a.total_feedbacks === 0);

  if (ranked.length === 0) {
    console.log("⚠️  Ningún agente con score/feedbacks en este batch.");
    console.log("    (Los ERC-8004 de BNB están recién lanzados — la mayoría tiene 0 feedbacks aún)\n");
  }

  // Mostrar los rankeados primero
  if (ranked.length > 0) {
    console.log("═══ AGENTES CON REPUTACIÓN ═══\n");
    for (const a of ranked) {
      const wallet = a.agent_wallet ?? a.owner_address ?? "—";
      const x402 = a.x402_supported ? "x402✓" : "";
      const protos = (a.supported_protocols ?? []).join(",") || "—";
      console.log(`📛 ${a.name}`);
      console.log(`   token_id: ${a.token_id}`);
      console.log(`   wallet:   ${wallet}`);
      console.log(`   score:    ${a.total_score?.toFixed(1)} | feedbacks: ${a.total_feedbacks} | stars: ${a.star_count}`);
      console.log(`   health:   ${a.health_score?.toFixed(1) ?? "—"} | protocols: ${protos} ${x402}`);
      console.log(`   url:      https://8004scan.io/agents/${CHAIN_ID}/${a.token_id}`);
      console.log(`   api:      https://api.8004scan.io/api/v1/agents/${CHAIN_ID}/${a.token_id}\n`);
    }
  }

  // Mostrar muestra de los fresh (recién registrados)
  if (fresh.length > 0) {
    console.log(`═══ RECIÉN REGISTRADOS (muestra, ${fresh.length} en este batch) ═══\n`);
    for (const a of fresh.slice(0, 10)) {
      const wallet = a.agent_wallet ?? a.owner_address ?? "—";
      const x402 = a.x402_supported ? " [x402]" : "";
      const desc = (a.description ?? "").slice(0, 80);
      console.log(`🆕 ${a.name}${x402}`);
      console.log(`   ${desc}${desc.length >= 80 ? "..." : ""}`);
      console.log(`   wallet: ${wallet} | token: ${a.token_id}`);
      console.log(`   url: https://8004scan.io/agents/${CHAIN_ID}/${a.token_id}\n`);
    }
    console.log(`   ... y ${fresh.length - 10} más en este batch.\n`);
  }

  console.log(`\n💡 Para contratar uno vía ERC-8183:`);
  console.log(`   import { hireErc8183Agent, BNB } from "@altananetwork/sdk";`);
  console.log(`   await hireErc8183Agent(wallet, signer, {`);
  console.log(`     provider: "<wallet de arriba>",`);
  console.log(`     task: "<tu tarea>",`);
  console.log(`     budget: 100_000_000_000_000_000n, // 0.1 $U`);
  console.log(`   }, { network: BNB });`);
} catch (err) {
  console.error(`❌ Error: ${err.message}`);
  process.exit(1);
}
