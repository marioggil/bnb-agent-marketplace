# ERC-8004 BSC Data Field Explorer

Prototipo local para explorar los campos de datos del protocolo
**ERC-8004** (agentes on-chain con reputación) en **BNB Smart Chain**, apuntado al
marketplace de agentes del hackathon **"Build the Era"**.

- Sin dependencias externas: usa `fetch` nativo de Node **>= 18**. No se instala nada.
- Todos los datos provienen de la **API pública de 8004scan**, sin ninguna
  fuente de indexación intermedia.

## Requisitos

- Node.js >= 18 (verificá con `node --version`).

## Setup

```bash
cp .env.example .env
```

Opciones que reconoce el `.env`:

| Variable        | Default                             | Uso                                |
| --------------- | ----------------------------------- | ---------------------------------- |
| `8004SCAN_BASE` | `https://8004scan.io/api/v1/public` | Base de la API pública de 8004scan |

El `.env` es opcional: sin él, todos los scripts apuntan a la base por defecto.

## Cómo correr

```bash
npm run stats    # stats globales del protocolo
npm run agents   # tabla de agentes de BSC (default 30 items, pasá un número para más)
npm run detail 252698   # detalle completo del agente con token_id 252698 (JSON)
```

Equivalencias directas con `node`:

```bash
node stats.mjs                 # stats globales del protocolo
node agents-bsc.mjs [limit]    # tabla de agentes de BSC (default 30 items)
node agent-detail.mjs <tokenId> [chainId]   # detalle completo de un agente (JSON)
```

### Contratos de referencia (BSC mainnet)

- IdentityRegistry: `0x8004A169FB4a3325136EB29fA0ceB6D2e539a432`
- ReputationRegistry: `0x8004BAa17C55a88189AE136b182e5fdA19dE9b63`

`agent_id` canónico: `56:0x8004...:tokenId` (chainId:registry:tokenId).

## Pro tier de 8004scan (acceso de mayor cuota / endpoints premium)

8004scan ofrece un tier Pro para accesos con más límites y endpoints
avanzados. Para los proyectos del hackathon hay acceso Pro **gratuito**:

1. Creá una API key en <https://8004scan.io/developers>.
2. Completá el formulario de solicitud:

   **https://forms.gle/jQevEPCAacBXaKG79**

Llená el formulario con tu proyecto (name, email, uso previsto). Cuando te
aprueben, usá tu API key con los requests de la API 8004scan.

> Sin Pro tier los endpoints básicos usados por estos scripts **igual funcionan**,
> así que podés correr todo sin key.

## Fuentes de datos

Toda la información proviene de la **API pública de 8004scan**
(`https://8004scan.io/api/v1/public`):

- `/stats` — métricas globales del protocolo.
- `/agents?limit=N&chain_id=56` — listado de agentes.
- `/agents/{chainId}/{tokenId}` — detalle completo de un agente.

Los datos se obtienen únicamente de esta API, sin ninguna fuente de indexación
intermedia. No se necesita ninguna key ni credencial para los endpoints usados.

## Qué hace cada script

| Script            | Qué consulta / imprime                                                                 |
| ----------------- | -------------------------------------------------------------------------------------- |
| `stats.mjs`       | `GET /stats`: total de agents, feedbacks, score promedio, nuevos diarios, cadenas con `has_registry=true`. |
| `agents-bsc.mjs`  | `GET /agents?limit=N&chain_id=56`: pide a la API y filtra localmente `chain_id===56`; imprime tabla legible (name, token_id, x402_supported, average_score, total_feedbacks, owner abreviado, created_at). |
| `agent-detail.mjs`| `GET /agents/{chainId}/{tokenId}`: detalle completo de UN agente como JSON formateado. |
| `env.mjs`         | Helper compartido: carga `.env`, base URL, `GET` JSON con manejo de errores. |