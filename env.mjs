import { readFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";
import process from "node:process";

export function loadEnv() {
  const envPath = resolve(process.cwd(), ".env");
  if (existsSync(envPath)) {
    for (const line of readFileSync(envPath, "utf8").split("\n")) {
      const m = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$/);
      if (m && process.env[m[1]] === undefined) {
        process.env[m[1]] = m[2];
      }
    }
  }
}

export function base() {
  return (process.env["8004SCAN_BASE"] || "https://8004scan.io/api/v1/public").replace(/\/+$/, "");
}

export async function getJson(path) {
  const url = `${base()}${path}`;
  const res = await fetch(url);
  if (!res.ok) {
    throw new Error(`HTTP ${res.status} for ${url}`);
  }
  const json = await res.json();
  if (!json.success) {
    throw new Error(`API error: ${JSON.stringify(json.error || json)}`);
  }
  return json.data;
}