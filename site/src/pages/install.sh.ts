import { readFile } from 'node:fs/promises';

export const prerender = true;

export async function GET() {
  return new Response(await readFile(new URL('../../../scripts/install-marivo.sh', import.meta.url)));
}
