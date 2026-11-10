export function sanitizeTerraformDiagnostic(value) {
  let text = typeof value === 'string' ? value : '';
  for (const [name, secret] of Object.entries(process.env)) {
    if (/secret|token|password|credential|private_key|access_key|authorization/i.test(name) && secret.length >= 4) {
      text = text.split(secret).join('[REDACTED]');
    }
  }
  return text
    .replace(/\u001b\[[0-?]*[ -/]*[@-~]/g, '')
    .replace(/-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?(?:-----END [^-]*PRIVATE KEY-----|$)/g, '[REDACTED]')
    .replace(/\b(?:AKIA|ASIA)[A-Z0-9]{16}\b/g, '[REDACTED]')
    .replace(/\beyJ[A-Za-z0-9_-]*\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b/g, '[REDACTED]')
    .replace(/([a-z][a-z0-9+.-]*:\/\/)[^\s/@]+:[^\s/@]+@/gi, '$1[REDACTED]@')
    .replace(/(https?:\/\/[^\s?"'<>]+)\?[^\s"'<>]*/gi, '$1?[REDACTED]')
    .replace(/\b(authorization\s*[:=]\s*)(?:Bearer|Basic)\s+[^\s,;]+/gi, '$1[REDACTED]')
    .replace(/(["']?\b[\w.-]*(?:password|passwd|secret|token|credential|private_key|access_key|authorization|jwt)[\w.-]*["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,;]+)/gi, '$1[REDACTED]')
    .replace(/^.*\b(?:\d+\s*:\s|[+~-]\s+[\w".-]+\s*=).*$/gm, '[source values withheld]')
    .replace(/[\u0000-\u0008\u000b-\u001f\u007f]/g, '')
    .slice(0, 16384);
}

export function terraformDiagnostics(stdout, stderr, json = false) {
  const diagnostics = [];
  if (json) {
    try {
      const document = JSON.parse(stdout);
      for (const diagnostic of (Array.isArray(document.diagnostics) ? document.diagnostics : []).slice(0, 50)) {
        if (!diagnostic || typeof diagnostic !== 'object') continue;
        const entry = {
          severity: ['error', 'warning'].includes(diagnostic.severity) ? diagnostic.severity : 'error',
          summary: sanitizeTerraformDiagnostic(diagnostic.summary),
          detail: sanitizeTerraformDiagnostic(diagnostic.detail),
        };
        if (typeof diagnostic.range?.filename === 'string') {
          entry.range = { filename: sanitizeTerraformDiagnostic(diagnostic.range.filename) };
          if (Number.isInteger(diagnostic.range.start?.line) && diagnostic.range.start.line > 0) {
            entry.range.start = { line: diagnostic.range.start.line };
          }
        }
        diagnostics.push(entry);
      }
    } catch {}
  }
  const textFailure = !json ? stdout.match(/(?:^|\n)[^\S\n]*[│╷]?\s*Error:[\s\S]*/)?.[0] || '' : '';
  const output = sanitizeTerraformDiagnostic([textFailure, stderr].filter(Boolean).join('\n')).trim();
  if (output) diagnostics.push({ severity: 'error', summary: 'Terraform command output', detail: output });
  if (!diagnostics.length) diagnostics.push({ severity: 'error', summary: 'Terraform command failed',
    detail: 'No usable diagnostics returned. Review the Terraform configuration and provider installation.' });
  return diagnostics;
}

export class TerraformCommandError extends Error {
  constructor(result) {
    super(result.reason);
    this.exitCode = result.status;
    this.diagnostics = result.diagnostics;
  }
}

export function formatTerraformDiagnostic(diagnostic) {
  const location = diagnostic.range
    ? ` (${diagnostic.range.filename}${diagnostic.range.start ? `:${diagnostic.range.start.line}` : ''})` : '';
  return `${diagnostic.severity}: ${diagnostic.summary}${location}\n${diagnostic.detail}`;
}
