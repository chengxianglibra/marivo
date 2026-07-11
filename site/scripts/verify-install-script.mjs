import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

const repoRoot = fileURLToPath(new URL('../..', import.meta.url));
const [source, output] = await Promise.all([
  readFile(new URL('scripts/install-marivo.sh', `file://${repoRoot}/`)),
  readFile(new URL('site/dist/install.sh', `file://${repoRoot}/`)),
]);

if (!source.equals(output)) {
  throw new Error('site/dist/install.sh does not match scripts/install-marivo.sh');
}

console.log('Verified install script output.');
