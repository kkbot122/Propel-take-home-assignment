import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

const nginxConfig = readFileSync(resolve(process.cwd(), 'nginx.conf'), 'utf8')

describe('nginx backend proxy', () => {
  it('re-resolves the Docker backend after container replacement', () => {
    expect(nginxConfig).toContain('resolver 127.0.0.11 valid=5s ipv6=off;')
    expect(nginxConfig).toContain('set $backend_origin http://backend-api:8000;')
    expect(nginxConfig.match(/proxy_pass \$backend_origin\$request_uri;/g)).toHaveLength(4)
    expect(nginxConfig).not.toContain('proxy_pass http://backend-api:8000')
  })
})
