/**
 * 配置加载
 *
 * 查找顺序：
 *   1. --config 指定的路径
 *   2. ./config.json（项目内覆盖，便于一个工作区一套参数）
 *   3. ~/.config/edr-cloud-recorder/config.json（用户级默认，遵循 XDG）
 *
 * 凭据默认放在用户级目录而不是项目目录，因为项目目录常常就是仓库目录 ——
 * .gitignore 挡不住 git add -A，而放在 ~/.config 下根本不会被任何仓库看见。
 * 也可以留空，回退到环境变量 REC_USER / REC_PASSWORD。
 */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

export const USER_CONFIG = path.join(
  process.env.XDG_CONFIG_HOME || path.join(os.homedir(), '.config'),
  'edr-cloud-recorder',
  'config.json',
);

export function loadConfig(argv = process.argv) {
  const i = argv.indexOf('--config');
  const explicit = i >= 0 && argv[i + 1] ? path.resolve(argv[i + 1]) : null;
  if (explicit && !fs.existsSync(explicit)) {
    throw new Error(`--config 指定的文件不存在：${explicit}`);
  }
  const candidate = explicit ?? [path.resolve('config.json'), USER_CONFIG].find((p) => fs.existsSync(p));
  if (!candidate) return { _path: null };

  let cfg;
  try {
    cfg = JSON.parse(fs.readFileSync(candidate, 'utf-8'));
  } catch (e) {
    throw new Error(`config.json 解析失败（${candidate}）：${e.message}`);
  }

  // 只在真的存了密码时才检查权限 —— 没有秘密就不必打扰用户
  if (cfg.auth?.password) {
    const mode = fs.statSync(candidate).mode & 0o077;
    if (mode) {
      console.warn(`⚠ ${candidate} 存有密码但权限过宽，建议：chmod 600 ${candidate}`);
    }
  }

  cfg._path = candidate;
  return cfg;
}

/** 凭据：配置优先，回退环境变量。返回的对象不要打印。 */
export function resolveAuth(cfg = {}) {
  return {
    user: cfg.auth?.user || process.env.REC_USER || '',
    password: cfg.auth?.password || process.env.REC_PASSWORD || '',
  };
}

/** 配置里的值作为默认，命令行参数优先级更高 */
export function withDefaults(cfg, args) {
  const argOf = (k, d) => {
    const i = args.indexOf(k);
    return i >= 0 && args[i + 1] ? args[i + 1] : d;
  };
  const base = cfg.baseUrl && cfg.entryPath ? cfg.baseUrl.replace(/\/$/, '') + cfg.entryPath : cfg.baseUrl;
  return {
    url: argOf('--url', process.env.REC_URL || base),
    apiFilter: argOf('--api', cfg.record?.apiFilter || null),
    outDir: argOf('--out', cfg.record?.outDir || 'recordings'),
    chromeBin: process.env.REC_CHROME_BIN || cfg.browser?.executablePath || null,
  };
}
