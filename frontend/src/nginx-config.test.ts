import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

const nginxConfig = readFileSync(resolve(process.cwd(), 'nginx.conf'), 'utf8')

describe('nginx backend proxy', () => {
  it('re-resolves the Docker backend after container replacement', () => {
    expect(nginxConfig).toContain('resolver ${NGINX_RESOLVER} valid=5s;')
    expect(nginxConfig).toContain('set $backend_origin ${BACKEND_ORIGIN};')
    expect(nginxConfig.match(/proxy_pass \$backend_origin\$request_uri;/g)).toHaveLength(5)
    expect(nginxConfig).not.toContain('proxy_pass http://backend-api:8000')
  })

  it('sets browser security headers and preserves the bounded batch body limit', () => {
    expect(nginxConfig).toContain('add_header X-Content-Type-Options "nosniff" always;')
    expect(nginxConfig).toContain('add_header X-Frame-Options "DENY" always;')
    expect(nginxConfig).toContain('location = /api/telemetry/batch {')
    expect(nginxConfig).toContain('client_max_body_size 1m;')
  })
})
