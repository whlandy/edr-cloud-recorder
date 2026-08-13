/**
 * 配置加载
 *
 * 查找顺序：--config 指定的路径 > cwd/config.json > 无配置。
 *
 * 关于凭据：config.json 里可以填 auth.user / auth.password，但**这个文件绝不能进版本库**。
 * 加载器会在文件权限过宽（组或其他用户可读）时提醒，因为这类文件被顺手 commit 或
 * 被同机其他账号读到是最常见的泄露方式。
 * 不想落盘就留空，回退到环境变量 REC_USER / REC_PASSWORD。
 */
import fs from 'node:fs';
import path from 'node:path';

export function loadConfig(argv = process.argv) {
  const i = argv.indexOf('--config');
  const explicit = i >= 0 && argv[i + 1] ? path.resolve(argv[i + 1]) : null;
  const candidate = explicit ?? path.resolve('config.json');

  if (!fs.existsSync(candidate)) {
    if (explicit) throw new Error(`--config 指定的文件不存在：${explicit}`);
    return { _path: null };
  }

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
