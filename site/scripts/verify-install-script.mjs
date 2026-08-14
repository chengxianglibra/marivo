import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

const repoRoot = fileURLToPath(new URL('../..', import.meta.url));
const [source, output, cnSource, cnOutput] = await Promise.all([
  readFile(new URL('scripts/install-marivo.sh', `file://${repoRoot}/`)),
  readFile(new URL('site/dist/install.sh', `file://${repoRoot}/`)),
  readFile(new URL('site/public/install-marivo-cn.sh', `file://${repoRoot}/`)),
  readFile(new URL('site/dist/install-marivo-cn.sh', `file://${repoRoot}/`)),
]);

if (!source.equals(output)) {
  throw new Error('site/dist/install.sh does not match scripts/install-marivo.sh');
}

function replaceExactlyOnce(value, from, to) {
  const first = value.indexOf(from);
  if (first === -1 || first !== value.lastIndexOf(from)) {
    throw new Error(`Expected exactly one installer fragment: ${from}`);
  }
  return value.replace(from, to);
}

let expectedCnSource = source.toString();
expectedCnSource = replaceExactlyOnce(
  expectedCnSource,
  'readonly DEFAULT_MARIVO_EXTRAS="duckdb,trino,clickhouse"\n',
  'readonly DEFAULT_MARIVO_EXTRAS="duckdb,trino,clickhouse"\n' +
    'readonly PYPI_INDEX_URL="https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple/"\n' +
    'readonly UV_PYTHON_INSTALL_MIRROR="https://registry.npmmirror.com/-/binary/python-build-standalone"\n',
);
expectedCnSource = replaceExactlyOnce(
  expectedCnSource,
  '    "$uv_bin" python install "$MIN_PYTHON" >&2',
  '    UV_PYTHON_INSTALL_MIRROR="$UV_PYTHON_INSTALL_MIRROR" "$uv_bin" python install "$MIN_PYTHON" >&2',
);
expectedCnSource = replaceExactlyOnce(
  expectedCnSource,
  '        "$uv_bin" python install "$PYTHON_SPEC" >&2',
  '        UV_PYTHON_INSTALL_MIRROR="$UV_PYTHON_INSTALL_MIRROR" "$uv_bin" python install "$PYTHON_SPEC" >&2',
);
expectedCnSource = replaceExactlyOnce(
  expectedCnSource,
  '    "$uv_bin" pip install --python "$VENV_PYTHON" --upgrade "$package_spec"',
  '    UV_INDEX_URL="$PYPI_INDEX_URL" "$uv_bin" pip install --python "$VENV_PYTHON" --upgrade "$package_spec"',
);

if (cnSource.toString() !== expectedCnSource) {
  throw new Error(
    'site/public/install-marivo-cn.sh must differ from scripts/install-marivo.sh only by the domestic Python and PyPI sources',
  );
}

if (!cnSource.equals(cnOutput)) {
  throw new Error(
    'site/dist/install-marivo-cn.sh does not match site/public/install-marivo-cn.sh',
  );
}

console.log('Verified standard and Chinese install script outputs.');
